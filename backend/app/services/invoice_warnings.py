"""Compute warnings and fraud flags for invoices — persisted on write.

Also creates exception records for issues that need human resolution.
"""

import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.services.po_matching import match_invoice_to_po

logger = logging.getLogger(__name__)

# ---------- Fraud rules: per-org tunable thresholds -----------------------
#
# Defaults are conservative — they should never fire on a healthy AP flow.
# Org admins override via PATCH /api/organization with `settings.fraud_rules`.
# Every rule key listed here is honored by `_fraud_config()`; unknown keys
# are ignored so we can ship new rules without a settings migration.

DEFAULT_FRAUD_RULES: dict = {
    # Master switch per rule. False suppresses both the warning and the
    # exception so an org can opt out of noisy rules without our help.
    "round_amount_enabled": True,
    "future_date_enabled": True,
    "bank_change_enabled": True,
    "stat_anomaly_enabled": True,
    "rush_payment_enabled": True,
    "new_vendor_large_enabled": True,
    "personal_email_enabled": True,
    "llm_anomaly_enabled": False,  # opt-in: costs an LLM call per invoice
    # Threshold knobs. Whatever the org sets here drives the warning.
    "round_amount_min": "1000",  # amounts >= this AND % 1000 == 0 flag
    "rush_payment_max_days": 3,  # due_date within N days of invoice_date
    "new_vendor_max_age_days": 30,  # vendor created within N days
    "new_vendor_large_amount": "10000",
    "stat_anomaly_sigma": 2.0,  # amount > mean + N*stdev
    "stat_anomaly_min_history": 3,  # need at least N prior approved invoices
    # Generic personal-email domains — case-insensitive match on the
    # vendor's email host. Anything outside this list is treated as a
    # business address (not perfect, but good enough as a flag).
    "personal_email_domains": [
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "aol.com",
        "icloud.com",
        "protonmail.com",
        "proton.me",
        "live.com",
        "msn.com",
        "yandex.com",
        "mail.com",
        "gmx.com",
    ],
}


def _fraud_config(org_settings: dict | None) -> dict:
    """Merge org overrides over the defaults. Org settings.fraud_rules
    can omit keys to inherit; unknown keys are dropped silently."""
    overrides = (org_settings or {}).get("fraud_rules") or {}
    merged = dict(DEFAULT_FRAUD_RULES)
    for k, v in overrides.items():
        if k in merged:
            merged[k] = v
    return merged


def _status_str(status) -> str:
    """Normalize an invoice status into a plain string.

    Handles three shapes that reach us in practice:
      - `InvoiceStatus` (StrEnum on the SQLA model) — `.value` works
        but `str(...)` is also correct since StrEnum inherits from str.
      - Plain string, e.g. after `setattr(invoice, "status", "x")` in
        `update_invoice` — has no `.value` attribute. This was a real
        500 in production until this helper landed.
      - Test-fixture mocks like `SimpleNamespace(value=...)` — no
        `__str__` override; `getattr(..., 'value', ...)` covers them.
    """
    return getattr(status, "value", status) if not isinstance(status, str) else status


async def refresh_warnings(
    db: AsyncSession, invoice: Invoice, *, org_settings: dict | None = None
) -> list[dict]:
    """Recompute warnings for a single invoice and persist them on the row.

    Also creates exception records for actionable issues.

    `org_settings` drives the configurable fraud rules. When omitted (the
    common case from existing call sites that haven't threaded it
    through), the defaults in `DEFAULT_FRAUD_RULES` are used.
    """
    warnings: list[dict] = []
    cfg = _fraud_config(org_settings)

    # Missing required fields
    if not invoice.vendor_name or not invoice.vendor_name.strip():
        warnings.append(
            {"type": "missing_field", "severity": "error", "message": "Missing vendor name"}
        )
    if not invoice.invoice_number or not invoice.invoice_number.strip():
        warnings.append(
            {"type": "missing_field", "severity": "error", "message": "Missing invoice number"}
        )
    if invoice.amount is None or invoice.amount <= 0:
        warnings.append(
            {"type": "missing_field", "severity": "error", "message": "Missing or zero amount"}
        )

    # Duplicate detection — check if another invoice has the same vendor + invoice #
    if invoice.vendor_name and invoice.invoice_number:
        dup_count = await db.execute(
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.vendor_name == invoice.vendor_name,
                Invoice.invoice_number == invoice.invoice_number,
                Invoice.id != invoice.id,
            )
        )
        if (dup_count.scalar() or 0) > 0:
            warnings.append(
                {
                    "type": "duplicate",
                    "severity": "warning",
                    "message": "Duplicate invoice number for this vendor",
                }
            )
            await _ensure_exception(
                db,
                invoice,
                "duplicate",
                "warning",
                "Duplicate invoice number for this vendor",
                org_settings=org_settings,
            )

    # Fraud: round amounts (legacy rule; configurable threshold)
    if cfg["round_amount_enabled"] and invoice.amount and invoice.amount > 0:
        threshold = Decimal(str(cfg["round_amount_min"]))
        if invoice.amount >= threshold and invoice.amount % 1000 == 0:
            warnings.append(
                {
                    "type": "fraud_round_amount",
                    "severity": "info",
                    "message": f"Round amount: {invoice.amount}",
                }
            )
            await _ensure_exception(
                db,
                invoice,
                "fraud_flag",
                "info",
                f"Suspicious round amount: ${invoice.amount}",
                org_settings=org_settings,
            )

    # Fraud: future invoice date
    if cfg["future_date_enabled"] and invoice.invoice_date and invoice.invoice_date > date.today():
        warnings.append(
            {
                "type": "fraud_future_date",
                "severity": "warning",
                "message": "Invoice date is in the future",
            }
        )
        await _ensure_exception(
            db,
            invoice,
            "fraud_flag",
            "warning",
            "Invoice date is in the future",
            org_settings=org_settings,
        )

    # Fraud: rush payment pattern. Very short window between invoice_date
    # and due_date is a classic social-engineering signal — fraudsters
    # push for "pay today" to skip controls.
    if (
        cfg["rush_payment_enabled"]
        and invoice.invoice_date
        and invoice.due_date
        and (invoice.due_date - invoice.invoice_date).days <= int(cfg["rush_payment_max_days"])
        and (invoice.due_date - invoice.invoice_date).days >= 0
    ):
        days = (invoice.due_date - invoice.invoice_date).days
        msg = f"Rush payment: due in {days} day(s) of invoice date"
        warnings.append({"type": "fraud_rush_payment", "severity": "warning", "message": msg})
        await _ensure_exception(
            db, invoice, "fraud_flag", "warning", msg, org_settings=org_settings
        )

    # Past-due flag (informational, not fraud — but lives in the same block).
    if (
        invoice.due_date
        and invoice.due_date < date.today()
        and _status_str(invoice.status) in ("new", "pending", "ready_for_review")
    ):
        warnings.append(
            {"type": "past_due", "severity": "warning", "message": "Invoice is past due"}
        )

    # Vendor-scoped fraud rules. All require `vendor_id` so we can pull
    # historical context — no point comparing the new invoice to itself.
    if invoice.vendor_id:
        from app.models.vendor import Vendor

        v_result = await db.execute(select(Vendor).where(Vendor.id == invoice.vendor_id))
        vendor = v_result.scalar_one_or_none()

        if vendor is not None:
            # Unverified vendor (existing rule)
            if vendor.status == "unverified":
                warnings.append(
                    {
                        "type": "unverified_vendor",
                        "severity": "warning",
                        "message": "Vendor is unverified",
                    }
                )
                await _ensure_exception(
                    db,
                    invoice,
                    "unverified_vendor",
                    "warning",
                    "Invoice linked to an unverified vendor",
                    org_settings=org_settings,
                )

            # Personal-email-domain flag. The vendor's email is set during
            # onboarding/sync; if it falls in a generic domain list, the
            # invoice rides through with extra scrutiny.
            if cfg["personal_email_enabled"] and vendor.email:
                _, _, host = vendor.email.partition("@")
                host = host.lower().strip()
                personal = {d.lower() for d in cfg["personal_email_domains"]}
                if host and host in personal:
                    msg = f"Vendor email uses personal domain: {host}"
                    warnings.append(
                        {"type": "fraud_personal_email", "severity": "warning", "message": msg}
                    )
                    await _ensure_exception(
                        db, invoice, "fraud_flag", "warning", msg, org_settings=org_settings
                    )

            # New-vendor + large-amount. A brand-new vendor making a huge
            # first ask is the canonical phishing pattern.
            if cfg["new_vendor_large_enabled"] and vendor.created_at and invoice.amount:
                vendor_age = datetime.now(vendor.created_at.tzinfo) - vendor.created_at
                large = Decimal(str(cfg["new_vendor_large_amount"]))
                if (
                    vendor_age <= timedelta(days=int(cfg["new_vendor_max_age_days"]))
                    and invoice.amount >= large
                ):
                    msg = (
                        f"New vendor (created {vendor_age.days} day(s) ago) submitting "
                        f"large invoice ${invoice.amount}"
                    )
                    warnings.append(
                        {"type": "fraud_new_vendor_large", "severity": "warning", "message": msg}
                    )
                    await _ensure_exception(
                        db, invoice, "fraud_flag", "warning", msg, org_settings=org_settings
                    )

            # Bank-account / remit-to change. We compare the incoming
            # invoice's `remit_to_address` to the most recent approved
            # invoice's value for this vendor — a mid-relationship change
            # is the bank-redirect attack signature.
            if cfg["bank_change_enabled"] and invoice.remit_to_address:
                last_remit_q = await db.execute(
                    select(Invoice.remit_to_address)
                    .where(
                        Invoice.vendor_id == invoice.vendor_id,
                        Invoice.id != invoice.id,
                        Invoice.status.in_(
                            [
                                InvoiceStatus.approved.value,
                                InvoiceStatus.posted_in_erp.value,
                                InvoiceStatus.payment_scheduled.value,
                                InvoiceStatus.paid.value,
                            ]
                        ),
                        Invoice.remit_to_address.isnot(None),
                    )
                    .order_by(Invoice.created_at.desc())
                    .limit(1)
                )
                prior_remit = last_remit_q.scalar_one_or_none()
                if prior_remit and prior_remit.strip() != (invoice.remit_to_address or "").strip():
                    msg = "Remit-to address changed since the last approved invoice for this vendor"
                    warnings.append(
                        {"type": "fraud_bank_change", "severity": "error", "message": msg}
                    )
                    await _ensure_exception(
                        db, invoice, "fraud_flag", "error", msg, org_settings=org_settings
                    )

            # Statistical amount anomaly. Pull last N approved invoice
            # amounts; if the new one is more than `sigma` above the
            # mean, flag. Only kicks in once we have enough history —
            # otherwise every second invoice is "anomalous."
            if cfg["stat_anomaly_enabled"] and invoice.amount:
                hist_q = await db.execute(
                    select(Invoice.amount)
                    .where(
                        Invoice.vendor_id == invoice.vendor_id,
                        Invoice.id != invoice.id,
                        Invoice.status.in_(
                            [
                                InvoiceStatus.approved.value,
                                InvoiceStatus.posted_in_erp.value,
                                InvoiceStatus.payment_scheduled.value,
                                InvoiceStatus.paid.value,
                            ]
                        ),
                        Invoice.amount.isnot(None),
                    )
                    .order_by(Invoice.created_at.desc())
                    .limit(20)
                )
                amounts = [Decimal(str(a)) for a in hist_q.scalars().all() if a is not None]
                if len(amounts) >= int(cfg["stat_anomaly_min_history"]):
                    mean = sum(amounts) / Decimal(len(amounts))
                    variance = sum((a - mean) ** 2 for a in amounts) / Decimal(len(amounts))
                    stdev = variance.sqrt() if variance > 0 else Decimal(0)
                    threshold_amount = mean + Decimal(str(cfg["stat_anomaly_sigma"])) * stdev
                    if invoice.amount > threshold_amount and stdev > 0:
                        sigma_count = (Decimal(str(invoice.amount)) - mean) / stdev
                        msg = (
                            f"Amount ${invoice.amount} is {sigma_count:.1f}σ above this "
                            f"vendor's historical mean (${mean:.2f})"
                        )
                        warnings.append(
                            {
                                "type": "fraud_stat_anomaly",
                                "severity": "warning",
                                "message": msg,
                            }
                        )
                        await _ensure_exception(
                            db, invoice, "fraud_flag", "warning", msg, org_settings=org_settings
                        )

            # LLM anomaly detection (opt-in). Costs an LLM call; gated
            # behind a per-org flag. When the rule fires, the LLM's
            # explanation rides the warning message so the reviewer
            # sees *why* — vital for AI-derived flags to be actionable.
            if cfg["llm_anomaly_enabled"]:
                await _llm_anomaly_check(db, invoice, vendor, warnings, org_settings)

    # Missing data (no amount after extraction)
    has_missing = any(w["type"] == "missing_field" for w in warnings)
    if has_missing and _status_str(invoice.status) not in ("new",):
        await _ensure_exception(
            db,
            invoice,
            "missing_data",
            "error",
            "Required fields missing after extraction",
            org_settings=org_settings,
        )

    # PO matching — runs whenever the invoice has a po_number. The match
    # service handles 2-way (invoice vs PO) and 3-way (with goods receipt).
    # Result is persisted on `invoice.po_match` so the modal can render it
    # without re-running. Mismatches and missing POs raise exceptions for
    # the queue. Skip on draft `new` invoices that haven't been extracted yet.
    if invoice.po_number and _status_str(invoice.status) != "new":
        await _refresh_po_match(db, invoice, warnings, org_settings)
    else:
        invoice.po_match = None

    # Reporting-currency conversion — lock the invoice's amount into the org's
    # reporting (base) currency so multi-currency analytics roll up correctly.
    # The rate is snapshotted on the row (see currency_conversion) and never
    # recomputed for an already-locked row, so historical totals stay stable.
    # Best-effort: an FX failure must never block saving an invoice.
    await _refresh_reporting_amount(invoice, org_settings)

    # Persist
    invoice.warnings = warnings or None
    return warnings


async def _refresh_reporting_amount(invoice: Invoice, org_settings: dict | None) -> None:
    """Materialize `invoice.reporting_*` for the org's reporting currency.

    Imported lazily so the warning engine doesn't pull the FX stack on every
    import. Swallows FX/adapter errors (logged) — a missing rate leaves the
    reporting fields NULL and the rollup falls back to face value for that one
    row rather than failing the whole save."""
    if invoice.amount is None:
        return
    try:
        from app.services.currency_conversion import (
            materialize_reporting_amount,
            resolve_reporting_currency,
        )
        from app.services.fx_adapters import get_fx_adapter

        reporting_currency = resolve_reporting_currency(org_settings)
        fx_adapter = get_fx_adapter((org_settings or {}).get("fx"))
        await materialize_reporting_amount(
            invoice,
            reporting_currency=reporting_currency,
            fx_adapter=fx_adapter,
        )
    except Exception:  # noqa: BLE001 — best-effort; never break the save path
        logger.warning("reporting-currency materialization failed for invoice; left NULL")


async def _refresh_po_match(
    db: AsyncSession,
    invoice: Invoice,
    warnings: list[dict],
    org_settings: dict | None = None,
) -> None:
    """Run PO matching and append to warnings + exceptions on issues.

    Stores the structured result on `invoice.po_match` for UI rendering.
    Mutates `warnings` in place.
    """
    match = await match_invoice_to_po(db, invoice)
    invoice.po_match = asdict(match)

    if match.status == "no_po":
        warnings.append(
            {
                "type": "po_mismatch",
                "severity": "error",
                "message": f"PO {invoice.po_number} not found",
            }
        )
        await _ensure_exception(
            db,
            invoice,
            "po_mismatch",
            "error",
            f"Invoice references PO {invoice.po_number} but no matching PO exists",
            org_settings=org_settings,
        )
    elif match.status == "mismatch":
        # Lead with the most useful number — variance %.
        msg = (
            f"Amount variance {match.amount_variance_pct:+.1f}% vs PO {match.po_number} "
            f"(invoice ${invoice.amount} vs PO ${match.po_total:.2f})"
        )
        warnings.append({"type": "po_mismatch", "severity": "warning", "message": msg})
        await _ensure_exception(
            db, invoice, "po_mismatch", "warning", msg, org_settings=org_settings
        )
    elif match.status == "partial":
        # Partial 3-way receipt — informational. Reviewer needs to know but
        # it's not an error; goods may be in transit.
        msg = (
            f"Partial 3-way match — {match.match_type} match against PO {match.po_number}, "
            f"but only part of the ordered quantity has been received"
        )
        warnings.append({"type": "po_mismatch", "severity": "info", "message": msg})
        await _ensure_exception(db, invoice, "po_mismatch", "info", msg, org_settings=org_settings)


async def _ensure_exception(
    db: AsyncSession,
    invoice: Invoice,
    exception_type: str,
    severity: str,
    description: str,
    *,
    org_settings: dict | None = None,
) -> None:
    """Create an exception if one doesn't already exist for this invoice + type."""
    existing = await db.execute(
        select(func.count()).where(
            APException.invoice_id == invoice.id,
            APException.exception_type == exception_type,
            APException.status.in_(["open", "escalated"]),
        )
    )
    if (existing.scalar() or 0) > 0:
        return  # already exists

    # Auto-routing: org settings can map exception_type → user UUID
    # (and a default-SLA-hours per type). Both are optional — when
    # absent we fall back to a sensible default and leave the assignee
    # unset.
    exc_settings = (org_settings or {}).get("exceptions") or {}
    auto_assign = (exc_settings.get("auto_assign_by_type") or {}).get(exception_type)
    sla_map = exc_settings.get("sla_hours_by_type") or {}
    sla_hours = sla_map.get(exception_type, exc_settings.get("default_sla_hours"))

    assigned_to_user_id = None
    if auto_assign:
        try:
            import uuid as _uuid

            assigned_to_user_id = _uuid.UUID(auto_assign)
        except (TypeError, ValueError):
            assigned_to_user_id = None

    due_at = None
    if isinstance(sla_hours, (int, float)) and sla_hours > 0:
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        due_at = _dt.now(_UTC) + _td(hours=float(sla_hours))

    db.add(
        APException(
            invoice_id=invoice.id,
            exception_type=exception_type,
            severity=severity,
            description=description,
            status="open",
            organization_id=invoice.organization_id,
            entity_id=invoice.entity_id,  # exception follows its invoice (P2)
            assigned_to_user_id=assigned_to_user_id,
            due_at=due_at,
        )
    )


async def _llm_anomaly_check(
    db: AsyncSession,
    invoice: Invoice,
    vendor,
    warnings: list[dict],
    org_settings: dict | None,
) -> None:
    """Pull last-N approved invoices for this vendor, run LLM anomaly
    detection, and append a warning + exception when flagged.

    Lives in this file so it can share the SQL session and ensure-
    exception machinery; the actual prompt + LLM I/O is in
    `services.llm_fraud_detection`.
    """
    from app.config import settings as app_settings
    from app.services.llm_fraud_detection import (
        HISTORY_SIZE,
        detect_anomaly,
        invoice_to_candidate,
        invoice_to_history,
    )

    # API key resolution: org BYOK overrides the platform default.
    api_key = ((org_settings or {}).get("extraction") or {}).get(
        "api_key"
    ) or app_settings.anthropic_api_key
    if not api_key:
        return

    # Pull approved history (same set the stat-anomaly rule uses).
    hist_q = await db.execute(
        select(Invoice)
        .where(
            Invoice.vendor_id == vendor.id,
            Invoice.id != invoice.id,
            Invoice.status.in_(
                [
                    InvoiceStatus.approved.value,
                    InvoiceStatus.posted_in_erp.value,
                    InvoiceStatus.payment_scheduled.value,
                    InvoiceStatus.paid.value,
                ]
            ),
        )
        .order_by(Invoice.created_at.desc())
        .limit(HISTORY_SIZE)
    )
    history_invoices = list(hist_q.scalars().all())
    if not history_invoices:
        return

    # Order oldest → newest in the prompt for natural reading.
    history_invoices.reverse()

    candidate = invoice_to_candidate(invoice)
    history = [invoice_to_history(h) for h in history_invoices]
    candidate.vendor_name = invoice.vendor_name or vendor.name or "Unknown vendor"

    result = await detect_anomaly(candidate, history, api_key=api_key)
    if result.is_anomaly and result.reason:
        msg = f"AI-flagged anomaly: {result.reason}"
        warnings.append({"type": "fraud_llm_anomaly", "severity": "warning", "message": msg})
        await _ensure_exception(
            db, invoice, "fraud_flag", "warning", msg, org_settings=org_settings
        )
