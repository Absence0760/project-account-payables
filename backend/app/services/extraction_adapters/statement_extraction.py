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
from dataclasses import dataclass, field

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


def _is_identifier_candidate(token: str) -> bool:
    """Could this token be the row's invoice reference?

    Digit-bearing (a reference always carries digits) but not a bare short run
    — a leading row counter (``1``, ``2``) or a terms figure (``Net 30``) is
    digits without being an identifier. Deliberately shape-only: an all-digit
    invoice number is real, so this cannot demand a letter. Telling a real
    reference from a PO number is what the caller's exactly-one rule does.
    """
    if not any(ch.isdigit() for ch in token):
        return False
    return not (token.isdigit() and len(token) < 4)


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


@dataclass(frozen=True)
class StatementScan:
    """What the offline reader made of a statement's text layer.

    Two numbers, not one, because "how many rows did you skip?" has no honest
    single answer here. The reader walks every physical line — blank lines,
    the vendor block, column headers, ``Page 1 of 2``, the statement total —
    through the same loop as a real open item, and a count of everything it
    declined would report dozens of skips on a clean statement. That number
    would train a reviewer to ignore it, which is worse than not showing one.

    So the skip is CLASSIFIED where it happens, and only one class is reported:

    * **Not a row** (silent) — the line never looked like an open item. It had
      no identifier-shaped token, or nothing money-shaped followed one. Column
      headers, page furniture, totals and ``balance forward`` all land here by
      construction, which is why they were already being skipped correctly.
    * **Ambiguous** (:attr:`ambiguous_skips`) — the line DID look like an open
      item, and the reader refused to pick between two readings of it: two
      money columns (which one is the open balance?), or a second
      reference-shaped column left of the amount (which one is the invoice
      number?). This is the class a clerk needs to know about, because the
      run below is short by exactly this many supplier rows and our own
      invoices for them will surface as ``missing_on_their_side``.

    A clean ``number date amount`` statement therefore reports **zero**; an
    aging-bucket statement reports one per data row. The split IS the feature —
    a bare total would be noise.
    """

    lines: list[StatementLineExtraction] = field(default_factory=list)
    ambiguous_skips: int = 0


def scan_statement_text(text: str) -> StatementScan:
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

    Every skip is classified as it happens — see :class:`StatementScan` for why
    only the ambiguous class is counted and reported.
    """
    lines: list[StatementLineExtraction] = []
    ambiguous_skips = 0
    for physical in text.splitlines():
        row = physical.strip()
        if not row:
            continue  # not a row
        tokens = row.split()
        if len(tokens) < 2:
            continue  # not a row

        date_idx = next((i for i, t in enumerate(tokens) if _DATE_TOKEN.match(t)), None)

        number_idx = next(
            (
                i
                for i, token in enumerate(tokens)
                if i != date_idx and _is_identifier_candidate(token)
            ),
            None,
        )
        if number_idx is None:
            # Not a row: no identifier-shaped token at all. The column header
            # and `Page 1 of 2` land here.
            continue

        # A "reference" that is itself unambiguous MONEY is not one: the row is
        # a summary or total line, not an open item. Not a row — so it is
        # skipped silently and is not an ambiguous skip either.
        #
        #   Total                              1,800.50
        #   Total  1,200.00  850.50  410.00    2,460.50   <- aging footer
        #   Current: 1,200.00   Past due:        850.00   <- summary block
        #
        # The first two only ever reached the skip path by accident: one has
        # nothing after the money token it took as its reference, the other has
        # too many. The third has exactly one figure after it and was therefore
        # ACCEPTED — booking a fabricated open item keyed on "1,200.00" for
        # 850.00, which is the invented-money outcome this whole reader exists
        # to avoid. Testing the reference directly is what closes all three.
        #
        # Deliberately narrow: an all-digit invoice number (`100234`) is real,
        # and `_is_money` says no to it — money needs cents, a thousands
        # separator, or a currency symbol.
        if _is_money(tokens[number_idx]):
            continue

        trailing = [i for i in range(number_idx + 1, len(tokens)) if i != date_idx]
        # Unambiguous money first; a lone amount-shaped integer only when the
        # row printed no cents at all. Either way: exactly one, or skip.
        candidates = [i for i in trailing if _is_money(tokens[i])] or [
            i for i in trailing if _is_amount(tokens[i])
        ]
        if not candidates:
            # Not a row: nothing money-shaped follows the identifier. `Total
            # 1,800.50` and `Balance forward 500.00` take the money token AS
            # their identifier and then find nothing after it.
            continue
        if len(candidates) > 1:
            # Ambiguous: two money columns and nothing on the row says which is
            # the open balance. The aging-bucket layout is this case.
            ambiguous_skips += 1
            continue
        amount_idx = candidates[0]

        # ...and exactly ONE identifier-shaped token may sit to its LEFT, or the
        # row is skipped. Same reasoning as the money rule above, applied to the
        # match key instead of the figure: a PO/reference column or an aging
        # label prints left of the invoice number and is identical in shape to
        # it, so taking the FIRST one silently books the wrong key —
        #
        #   PO 4502  INV-1  2026-01-15  500.00   -> booked as invoice "4502"
        #   0-30     INV-1  2026-01-15  500.00   -> booked as invoice "0-30"
        #
        # Neither misreads the AMOUNT, which is why the two earlier column fixes
        # didn't catch them; both misroute the reconciliation. A wrong key is
        # softer than a wrong balance only while the amount+date fallback
        # happens to land — outside the date window, or on an amount collision,
        # it produces exactly the false discrepancy this reader exists to avoid.
        # Ambiguity resolves to skipping here too.
        identifiers = [
            i for i in range(amount_idx) if i != date_idx and _is_identifier_candidate(tokens[i])
        ]
        if len(identifiers) != 1:
            # Always MORE than one here, never zero — `number_idx` is itself an
            # identifier candidate left of the amount, so it is always in this
            # list. `!= 1` is written rather than `> 1` so the guard survives a
            # future change to how the identifier is chosen. Either way the row
            # looked like an open item, so it is the ambiguous class.
            ambiguous_skips += 1
            continue

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
            # Ambiguous for the same reason as the two-money-column case: this
            # only fires when a money token was chosen and something
            # amount-shaped still sits to its right, i.e. a second column.
            ambiguous_skips += 1
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
    return StatementScan(lines=lines, ambiguous_skips=ambiguous_skips)
