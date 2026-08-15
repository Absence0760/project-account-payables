"""Shared building blocks for the ``extract_statement`` adapter capability.

Three pieces, all pure and free of adapter imports (so the vision adapters can
import this without a cycle — ``ollama`` already imports ``claude_vision``):

* :data:`STATEMENT_EXTRACTION_PROMPT` — the one prompt every model-backed
  adapter sends, so Claude and a local Ollama model are asked for the SAME
  shape and a fix to the wording lands in both.
* :func:`parse_statement_payload` — the model's JSON → a
  :class:`StatementExtractionResult`, tolerant of a missing / malformed field
  and never raising.
* :func:`scan_statement_text` — a deterministic, offline reader for a
  statement's text layer. This is the ``mock`` adapter's stand-in for a model
  (same role the mock payment adapter's synthetic settlement plays), which is
  what makes the whole PDF-statement path exercisable on a dev laptop with no
  cloud credential. It is NOT a production statement parser: it reads the
  common ``number  date  amount`` row and deliberately gives up on anything
  else rather than guessing.

See ``backend/docs/vendor-statement-reconciliation.md`` § PDF intake.
"""

from __future__ import annotations

import re

from app.services.extraction_adapters.base import (
    STATEMENT_REASON_NO_LINES,
    STATEMENT_REASON_UNREADABLE,
    StatementExtractionResult,
    StatementLineExtraction,
)

STATEMENT_EXTRACTION_PROMPT = """You are reading a SUPPLIER STATEMENT OF OPEN \
ITEMS — the list of invoices one supplier believes are still unpaid, as of a \
statement date. This is NOT a single invoice: extract every open-item ROW.

Return a JSON object with exactly this structure:

```json
{
  "lines": [
    {
      "invoice_number": {"value": "string", "confidence": 0.95},
      "invoice_date": {"value": "YYYY-MM-DD or null", "confidence": 0.9},
      "amount": {"value": "decimal string", "confidence": 0.95},
      "status": {"value": "string or null", "confidence": 0.5}
    }
  ]
}
```

Rules:
- One entry per open-item row. Skip column headers, page furniture, subtotals,
  the statement total, and any "balance forward" line.
- "amount" is the OPEN balance for that row as a plain decimal string: no
  currency symbol, no thousands separators. A credit is negative — a
  parenthesised figure such as (250.00) means -250.00.
- Confidence is per field, between 0.0 and 1.0. Be honest about uncertainty:
  0.95-1.0 clearly printed, 0.8-0.94 legible, 0.5-0.79 partially obscured or
  guessed, null with 0.0 when the field is not present.
- NEVER invent a row that is not printed on the document. Return an empty
  "lines" array if this document is not a statement of open items.

Return ONLY the JSON object, no other text."""


def _field(item: dict, name: str) -> tuple[str | None, float]:
    """Read one ``{"value": ..., "confidence": ...}`` field, tolerating a bare
    scalar (some models drop the wrapper) and any junk shape."""
    if name not in item:
        return None, 0.0
    raw = item[name]
    if isinstance(raw, dict):
        value = raw.get("value")
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
    else:
        value, confidence = raw, 0.5
    if value is None:
        return None, 0.0
    text = str(value).strip()
    return (text or None), confidence


def parse_statement_payload(data: object, provider: str) -> StatementExtractionResult:
    """Turn a model's JSON payload into a :class:`StatementExtractionResult`.

    A row carrying neither an invoice number nor an amount has nothing to match
    on and is dropped (the same rule the CSV parser applies). A payload that
    isn't a ``{"lines": [...]}`` object at all is ``unreadable_response``; a
    well-formed payload with no usable row is ``no_lines_found`` — the caller
    surfaces those differently, because one is a provider problem and the other
    is "this document isn't a statement".
    """
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        return StatementExtractionResult(
            available=True,
            provider=provider,
            reason=STATEMENT_REASON_UNREADABLE,
            raw_response=data if isinstance(data, dict) else None,
        )

    lines: list[StatementLineExtraction] = []
    confidences: list[float] = []
    for item in data["lines"]:
        if not isinstance(item, dict):
            continue
        number, number_conf = _field(item, "invoice_number")
        line_date, _ = _field(item, "invoice_date")
        amount, amount_conf = _field(item, "amount")
        line_status, _ = _field(item, "status")
        if number is None and amount is None:
            continue
        row_conf = [c for c in (number_conf, amount_conf) if c > 0]
        confidence = sum(row_conf) / len(row_conf) if row_conf else 0.0
        confidences.append(confidence)
        lines.append(
            StatementLineExtraction(
                invoice_number=number,
                invoice_date=line_date,
                amount=amount,
                status=line_status,
                confidence=confidence,
                raw=item,
            )
        )

    if not lines:
        return StatementExtractionResult(
            available=True,
            provider=provider,
            reason=STATEMENT_REASON_NO_LINES,
            raw_response=data,
        )

    return StatementExtractionResult(
        available=True,
        success=True,
        lines=lines,
        overall_confidence=(sum(confidences) / len(confidences)) if confidences else 0.0,
        provider=provider,
        raw_response=data,
    )


# --------------------------------------------------------------------------- #
# Deterministic offline reader (the `mock` adapter's stand-in for a model)
# --------------------------------------------------------------------------- #

# A date column: 2026-01-15 / 01/20/2026 / 20-01-2026. Deliberately loose on
# ordering — the caller re-parses the token with the reconciliation engine's own
# forgiving date parser, so this only has to *recognise* a date, not read it.
_DATE_TOKEN = re.compile(r"^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}$")

# A money column: 1200 / 1,200.00 / $1,200.00 / -1200.00 / (250.00).
_AMOUNT_TOKEN = re.compile(r"^\(?[-+]?[$€£]?\d[\d,]*(?:\.\d{1,2})?\)?$")

# Confidence a heuristic text read is worth. Below every model's clearly-printed
# band on purpose: this reader recognises a shape, it does not read a document.
_SCAN_CONFIDENCE = 0.6


def _is_amount(token: str) -> bool:
    return bool(_AMOUNT_TOKEN.match(token))


def _is_money(token: str) -> bool:
    """Is this token unambiguously MONEY, rather than merely a number?

    Money on a statement carries a decimal fraction, a thousands separator, or a
    currency symbol. A bare integer does not: on a real statement row the bare
    integers are the payment-terms column (``Net 30``) and the aging-days column
    (``45``), both of which sit *before* the balance. Taking the first
    amount-shaped token without this distinction reads ``INV-1 2026-01-15 Net 30
    1,200.00`` as a 30.00 open item — silently wrong money, which is worse on
    this feature than no line at all.
    """
    if not _AMOUNT_TOKEN.match(token):
        return False
    core = token.strip("()+-")
    return "." in core or any(sym in core for sym in "$€£,")


def scan_statement_text(text: str) -> list[StatementLineExtraction]:
    """Read ``number [date] amount`` rows out of a statement's text layer.

    Deterministic and conservative. A row is kept only when an identifier token
    is followed by a money token — read strictly left to right:

    1. the identifier is the first digit-bearing token that isn't the date and
       isn't a bare run of fewer than four digits (that's a row counter or a
       page number, never an invoice reference — while ``1001`` legitimately
       is one);
    2. the amount is the row's one unambiguous MONEY token after it (see
       :func:`_is_money`). **Exactly one, or the row is skipped.** Falling back
       to a lone amount-shaped integer covers a statement that prints no cents.
    3. nothing amount-shaped may sit to the RIGHT of that amount, or the row is
       skipped — a terms (``Net 30``) or aging-days column prints BEFORE the
       balance, a second money column AFTER it, and the two are identical in
       shape, so position is what tells them apart.

    Ordering is what does the filtering. ``Total 1,800.50`` and
    ``Balance forward 500.00`` take the money token as their identifier and
    then find nothing after it; ``Page 1 of 2``'s only digits are bare short
    runs; the column header has no digits at all.

    **Why "exactly one" and not "the first".** A row with two money columns is
    ``number date invoice-amount balance-due`` or ``number date balance
    aging-bucket`` — and nothing on the row says which. Picking either produces
    a plausible figure that may be the wrong one, and a wrong open balance is
    exactly the failure this reader must not have. Skipping is loud instead:
    our own invoice for that row surfaces as ``missing_on_their_side``, a
    difference the clerk sees and chases. Every ambiguity here resolves to
    skipping. A multi-column or aging-bucket statement is the case this reader
    cannot resolve honestly, and the answer there is a vision provider
    (``ollama`` locally, ``claude_vision`` deployed), not a smarter guess.
    """
    lines: list[StatementLineExtraction] = []
    for physical in text.splitlines():
        row = physical.strip()
        if not row:
            continue
        tokens = row.split()
        if len(tokens) < 2:
            continue

        date_idx = next((i for i, t in enumerate(tokens) if _DATE_TOKEN.match(t)), None)

        number_idx = None
        for i, token in enumerate(tokens):
            if i == date_idx or not any(ch.isdigit() for ch in token):
                continue
            if token.isdigit() and len(token) < 4:
                continue
            number_idx = i
            break
        if number_idx is None:
            continue

        trailing = [i for i in range(number_idx + 1, len(tokens)) if i != date_idx]
        # Unambiguous money first; a lone amount-shaped integer only when the
        # row printed no cents at all. Either way: exactly one, or skip.
        candidates = [i for i in trailing if _is_money(tokens[i])] or [
            i for i in trailing if _is_amount(tokens[i])
        ]
        if len(candidates) != 1:
            continue
        amount_idx = candidates[0]

        # ...and nothing numeric may sit to the RIGHT of it. Position is the
        # only shape-independent signal separating the two mixed layouts:
        #
        #   INV-1  2026-01-15  Net 30    1,200.00   <- terms/aging, LEFT: fine
        #   INV-1  2026-01-15  1200.00   800        <- second column, RIGHT: skip
        #
        # The bare integer in both is identical in shape ("30" vs "800"), so
        # "not money" alone can't tell them apart. But a terms or aging-days
        # column is printed BEFORE the balance and a second money column AFTER
        # it, and only the second one makes which figure is open ambiguous.
        if any(_is_amount(tokens[i]) for i in trailing if i > amount_idx):
            continue

        lines.append(
            StatementLineExtraction(
                invoice_number=tokens[number_idx],
                invoice_date=tokens[date_idx] if date_idx is not None else None,
                amount=tokens[amount_idx],
                status=None,
                confidence=_SCAN_CONFIDENCE,
                raw={"text": row},
            )
        )
    return lines
