"""Mock assistant adapter — the local-first default.

Deterministically routes a natural-language message to ONE of the five fixed
tools via ordered keyword/intent rules, calls ``run_tool``, and formats a
templated answer. No LLM, no network, no key. Token counts are a deterministic
estimate so the usage meter + budget path are exercised identically to the
claude path.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.services.assistant.base import AssistantAdapter, AssistantReply, RunTool, ToolInvocation
from app.services.assistant.dispatcher import register_assistant_adapter

# ---------------------------------------------------------------------------
# Deterministic argument parsers (pure, unit-tested)
# ---------------------------------------------------------------------------

_STATUS_WORDS = {
    "approved": "approved",
    "rejected": "rejected",
    "paid": "paid",
    "pending": "pending",
    "posted": "posted_in_erp",
    "scheduled": "payment_scheduled",
    "new": "new",
}


def _parse_money(text: str) -> Decimal | None:
    m = re.search(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _parse_horizon(text: str) -> str:
    m = re.search(r"\b(7|14|30|60|90)\s*day", text)
    if m:
        return f"{m.group(1)}d"
    return "30d"


def _parse_granularity(text: str) -> str:
    if "month" in text:
        return "month"
    if "dai" in text or re.search(r"\bday\b", text):
        return "day"
    return "week"


def _parse_period(text: str) -> str:
    if "ytd" in text or "year to date" in text or "this year" in text:
        return "ytd"
    if "qtd" in text or "quarter" in text:
        return "qtd"
    if "mtd" in text or "month to date" in text or "this month" in text:
        return "mtd"
    if "last 30" in text or "30 day" in text:
        return "last_30d"
    if "last 90" in text or "90 day" in text:
        return "last_90d"
    if "last 12" in text or "12 month" in text or "trailing year" in text:
        return "last_12m"
    return "ytd"


def _parse_top_n(text: str, default: int = 10) -> int:
    m = re.search(r"top\s+(\d+)", text)
    if m:
        return max(1, min(25, int(m.group(1))))
    return default


def _parse_k(text: str, default: int = 5) -> int:
    m = re.search(r"top\s+(\d+)", text)
    if m:
        return max(1, min(15, int(m.group(1))))
    return default


def _parse_status_words(text: str) -> list[str]:
    found = []
    for word, value in _STATUS_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            found.append(value)
    return found


def _parse_vendor_name(text: str) -> str | None:
    m = re.search(r"\b(?:from|vendor|supplier)\s+([a-z0-9][a-z0-9 &.\-]{1,60})", text)
    if not m:
        return None
    name = m.group(1).strip()
    # Trim trailing filler that often follows the vendor name.
    name = re.split(r"\b(over|under|above|below|since|in|last|this|with|that|where)\b", name)[
        0
    ].strip(" .,")
    return name or None


def _strip_search_verb(message: str) -> str:
    pattern = re.compile(
        r"^\s*(?:find|search|look for|look up|show me invoices? (?:about|like|similar to)|"
        r"invoices? (?:about|like|similar to)|like|similar to)\s+",
        re.IGNORECASE,
    )
    stripped = pattern.sub("", message).strip()
    return stripped or message.strip()


# ---------------------------------------------------------------------------
# Intent routing (first match wins)
# ---------------------------------------------------------------------------


def route(message: str) -> tuple[str, dict]:
    """Return ``(tool_name, raw_args)`` for ``message`` (deterministic)."""
    text = message.lower()

    # 1. pending approvals — match the *queue* intent, not the bare status word
    # "approved" (which is a list_invoices filter). "awaiting/pending/to approve/
    # for approval/needs approval/my queue/waiting on me" all signal the queue,
    # as does the noun "approval(s)" paired with an ownership/wait cue
    # ("sitting on", "waiting on", "have I", "my", "haven't I") — e.g.
    # "which approvals have I been sitting on > 5 days?".
    if (
        any(
            kw in text
            for kw in (
                "awaiting",
                "pending approval",
                "to approve",
                "for approval",
                "needs approval",
                "need approval",
                "my queue",
                "approval queue",
                "waiting on me",
                "waiting for me",
                "sitting on",
                "sat on",
            )
        )
        or (re.search(r"\bpending\b", text) and "approv" in text)
        # "approval(s)" noun + a personal-queue cue. Guarded against the bare
        # status word "approved" (a list_invoices filter) by requiring the
        # "approval" stem to NOT be the past-tense "approved".
        or (
            re.search(r"\bapprovals?\b", text)
            and any(
                cue in text
                for cue in ("sitting", "waiting", "have i", "haven't i", " my ", "stuck", "i been")
            )
        )
    ):
        assignee = "anyone" if any(w in text for w in ("all", "everyone", "anyone")) else "me"
        return "list_pending_approvals", {"assignee": assignee}

    # 2. payment forecast (time-flavoured cash questions)
    if any(
        kw in text
        for kw in ("forecast", "cash", "cashflow", "due", "upcoming payment", "owe", "payable")
    ):
        return "get_payment_forecast", {
            "horizon": _parse_horizon(text),
            "granularity": _parse_granularity(text),
        }

    # 3. vendor spend
    if (
        any(
            kw in text
            for kw in (
                "spend",
                "top vendor",
                "vendor concentration",
                "supplier",
                "paid the most",
                "paying the most",
                "pay the most",
                "biggest vendor",
            )
        )
        or re.search(r"how much.*paid", text)
        # "which vendors are we paying the most" — vendor(s) noun + a spend verb.
        or re.search(r"vendors?\b.*\b(?:pay|paying|paid|spend|spent)\b", text)
        # "top N vendors" / "top vendors" (plural, with an optional count).
        or re.search(r"top\s+\d*\s*vendors?\b", text)
    ):
        return "get_vendor_spend", {
            "period": _parse_period(text),
            "top_n": _parse_top_n(text),
        }

    # 4. text / similarity search
    if any(
        kw in text for kw in ("find", "search", "look for", "similar", "show me invoices about")
    ) or re.search(r"\blike\b", text):
        return "find_invoices_by_text", {
            "query": _strip_search_verb(message),
            "k": _parse_k(text),
        }

    # 5. list_invoices (fallback)
    args: dict = {}
    statuses = _parse_status_words(text)
    if statuses:
        args["status"] = statuses
    vendor = _parse_vendor_name(text)
    if vendor:
        args["vendor_name"] = vendor
    over = re.search(r"\b(?:over|above|more than)\b\s*(\$?\s*[0-9][0-9,]*(?:\.\d+)?)", text)
    if over:
        amt = _parse_money(over.group(1))
        if amt is not None:
            args["amount_min"] = amt
    under = re.search(r"\b(?:under|below|less than)\b\s*(\$?\s*[0-9][0-9,]*(?:\.\d+)?)", text)
    if under:
        amt = _parse_money(under.group(1))
        if amt is not None:
            args["amount_max"] = amt
    return "list_invoices", args


# ---------------------------------------------------------------------------
# Answer templating (deterministic, per-tool)
# ---------------------------------------------------------------------------


def _fmt_money(value, currency: str = "") -> str:
    suffix = f" {currency}" if currency else ""
    return f"{value}{suffix}"


def _template_answer(tool: str, result: dict | None, error: str | None) -> str:
    if error:
        return "Sorry — I couldn't complete that request just now."
    result = result or {}

    if tool == "list_pending_approvals":
        total = result.get("total", 0)
        items = result.get("items", [])
        if not total:
            return "You have no invoices awaiting your approval."
        lines = [f"You have {total} invoice(s) awaiting your approval."]
        for it in items[:5]:
            lines.append(
                f"• {it['invoice_number']} — {it['vendor_name']} — "
                f"{_fmt_money(it['amount'], it['currency'])}"
            )
        return "\n".join(lines)

    if tool == "get_vendor_spend":
        vendors = result.get("vendors", [])
        currency = result.get("currency", "")
        if not vendors:
            return f"No vendor spend found for {result.get('period_label', 'that period')}."
        header = (
            f"Top {len(vendors)} vendors {result.get('period_label', '')}: "
            f"total {_fmt_money(result.get('total_spend', 0), currency)}."
        )
        lines = [header]
        for v in vendors:
            lines.append(
                f"• {v['vendor_name']} — {_fmt_money(v['amount'], currency)} ({v['share_pct']}%)"
            )
        return "\n".join(lines)

    if tool == "get_payment_forecast":
        buckets = result.get("buckets", [])
        currency = result.get("currency", "")
        if not buckets:
            return f"No projected outflow over the {result.get('horizon_label', 'horizon')}."
        lines = [
            f"Projected outflow over the {result.get('horizon_label', '')}: "
            f"{_fmt_money(result.get('total', 0), currency)}."
        ]
        for b in buckets:
            lines.append(
                f"• {b['period']}: {_fmt_money(b['amount'], currency)} ({b['count']} invoice(s))"
            )
        return "\n".join(lines)

    if tool == "find_invoices_by_text":
        matches = result.get("matches", [])
        if not matches:
            return "I couldn't find any similar invoices."
        lines = [f"Found {len(matches)} similar invoice(s)."]
        for m in matches:
            vendor = m.get("vendor_name") or "(unknown vendor)"
            lines.append(f"• {vendor} — similarity {m['similarity']:.2f} — {m['snippet']}")
        return "\n".join(lines)

    # list_invoices
    total = result.get("total", 0)
    items = result.get("items", [])
    if not total:
        return "No invoices match that query."
    lines = [f"{total} invoice(s) match."]
    for it in items[:10]:
        lines.append(
            f"• {it['invoice_number']} — {it['vendor_name']} — "
            f"{_fmt_money(it['amount'], it['currency'])} — {it['status']}"
        )
    return "\n".join(lines)


@register_assistant_adapter("mock")
class MockAssistantAdapter(AssistantAdapter):
    """Deterministic keyword router — the default, local-first adapter."""

    provider_name = "mock"

    async def respond(
        self,
        *,
        message: str,
        history: list[dict],
        tool_specs: list[dict],
        run_tool: RunTool,
    ) -> AssistantReply:
        tool_name, raw_args = route(message)
        invocation: ToolInvocation = await run_tool(tool_name, raw_args)
        answer = _template_answer(tool_name, invocation.result, invocation.error)
        # Deterministic token estimate so the meter/budget path runs identically.
        input_tokens = max(1, len(message) // 4)
        output_tokens = max(1, len(answer) // 4)
        return AssistantReply(
            answer=answer,
            tool_invocations=[invocation],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=self.provider_name,
        )

    async def test_connection(self) -> bool:
        return True
