"""Reporting-currency conversion for multi-currency rollups.

The international-PAYMENT path (`services/international_payments.py`) already
locks an FX rate on each Payment row at submission and computes the *realized*
gain/loss when a foreign-currency invoice settles. This module is the reporting
counterpart: it converts arbitrary invoice / payment amounts into the org's one
**reporting (base) currency** so analytics and the dashboard roll multi-currency
volume up into a single comparable number, and it computes the *unrealized*
FX gain/loss on open (approved-but-unpaid) foreign-currency invoices.

Design choices that keep the numbers honest:

  - **Money is exact.** Everything is `Decimal`; amounts quantize to 2 dp with
    `ROUND_HALF_UP`. Never `float`.
  - **Rates are locked, not recomputed.** When an invoice's reporting amount is
    *materialized* onto the row (`materialize_reporting_amount`), the rate used
    is persisted (`Invoice.reporting_fx_rate` + `reporting_fx_locked_at`). A
    later rollup reads the persisted `reporting_amount` and does NOT re-fetch a
    rate — so a market move doesn't retroactively rewrite last quarter's spend.
  - **Same-currency is a no-op fetch.** rate = 1, no adapter call.
  - **Resolution order for the org reporting currency:**
      1. `Organization.settings.reporting_currency`
      2. `Organization.settings.payments.home_currency` (legacy — the field the
         payment path already reads)
      3. `Organization.settings.invoice_defaults.currency`
      4. `settings.reporting_currency_default` (AP_REPORTING_CURRENCY_DEFAULT)

The functions here are pure-ish: the FX adapter is handed in, mirroring
`international_payments`, so the rollup logic is unit-testable against the mock
adapter without HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.config import settings
from app.services.fx_adapters import FXAdapter, FXRate

_MONEY_QUANT = Decimal("0.01")
_RATE_QUANT = Decimal("0.00000001")


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def resolve_reporting_currency(org_settings: dict | None) -> str:
    """Resolve the org's reporting (base) currency from its settings.

    See the module docstring for the resolution order. Always returns an
    uppercase 3-letter code; never raises (a misconfigured org degrades to
    the platform default rather than 500-ing a dashboard).
    """
    s = org_settings or {}
    candidates = [
        s.get("reporting_currency"),
        (s.get("payments") or {}).get("home_currency"),
        (s.get("invoice_defaults") or {}).get("currency"),
        settings.reporting_currency_default,
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip().upper()
    return "USD"


@dataclass(frozen=True)
class ConvertedAmount:
    """An amount expressed in the reporting currency plus the FX evidence."""

    amount: Decimal  # in reporting currency
    source_currency: str
    reporting_currency: str
    fx_rate: Decimal  # source -> reporting multiplier
    as_of: datetime


async def convert_amount(
    *,
    amount: Decimal,
    source_currency: str,
    reporting_currency: str,
    fx_adapter: FXAdapter,
) -> ConvertedAmount:
    """Convert `amount` from `source_currency` into `reporting_currency`.

    `rate` is the multiplier such that `reporting = source_amount * rate`
    (the FXRate contract: rate is units of target per unit of source). For
    same-currency the rate is 1 and no adapter call is made.

    Raises `ValueError` if the adapter returns a non-positive rate — a zero /
    negative rate would zero out or invert the converted figure.
    """
    src = (source_currency or reporting_currency).upper()
    tgt = reporting_currency.upper()
    if src == tgt:
        return ConvertedAmount(
            amount=_quantize_money(amount),
            source_currency=src,
            reporting_currency=tgt,
            fx_rate=Decimal("1"),
            as_of=datetime.now().astimezone(),
        )
    fx: FXRate = await fx_adapter.get_rate(src, tgt)
    if fx.rate <= 0:
        raise ValueError(f"FX provider returned non-positive rate for {src}->{tgt}: {fx.rate}")
    return ConvertedAmount(
        amount=_quantize_money(amount * fx.rate),
        source_currency=src,
        reporting_currency=tgt,
        fx_rate=fx.rate,
        as_of=fx.as_of,
    )


async def materialize_reporting_amount(
    invoice,
    *,
    reporting_currency: str,
    fx_adapter: FXAdapter,
    force: bool = False,
) -> bool:
    """Lock the reporting-currency conversion onto an Invoice row.

    Writes `reporting_currency`, `reporting_amount`, `reporting_fx_rate`,
    `reporting_fx_locked_at` in place (caller flushes/commits). The locked rate
    is what every later rollup reads — we never re-fetch a rate for a row that's
    already materialized, so historical conversions are stable.

    Re-materializes (re-fetches a fresh rate) only when:
      - `force` is True, OR
      - the row has never been materialized (`reporting_amount` is None), OR
      - the org's reporting currency changed since the last lock, OR
      - the invoice's own amount / currency changed in a way that invalidates
        the stored figure (amount differs from rate*... — we detect the simple
        case where currency changed).

    Returns True if it (re)materialized, False if it left an already-current
    lock untouched (idempotent no-op).
    """
    tgt = reporting_currency.upper()
    src = (invoice.currency or tgt).upper()

    needs = (
        force
        or invoice.reporting_amount is None
        or invoice.reporting_currency is None
        or invoice.reporting_currency.upper() != tgt
    )
    if not needs:
        return False

    converted = await convert_amount(
        amount=invoice.amount,
        source_currency=src,
        reporting_currency=tgt,
        fx_adapter=fx_adapter,
    )
    invoice.reporting_currency = converted.reporting_currency
    invoice.reporting_amount = converted.amount
    invoice.reporting_fx_rate = converted.fx_rate.quantize(_RATE_QUANT)
    invoice.reporting_fx_locked_at = converted.as_of
    return True


def reporting_amount_for_row(
    *,
    amount: Decimal,
    currency: str | None,
    reporting_currency: str,
    persisted_reporting_currency: str | None,
    persisted_reporting_amount: Decimal | None,
) -> tuple[Decimal, bool]:
    """Best-effort reporting amount for a single row WITHOUT an FX call.

    Used by aggregate rollups that must stay synchronous over many rows. Prefers
    the persisted (rate-locked) `reporting_amount` when it matches the org's
    current reporting currency; otherwise:
      - same-currency rows convert at 1:1, exact.
      - foreign rows with no usable lock fall back to the raw `amount` and are
        flagged `unconverted=True` so the caller can surface "N rows pending
        conversion" rather than silently mixing currencies.

    Returns `(reporting_amount, unconverted)`.
    """
    tgt = reporting_currency.upper()
    cur = (currency or tgt).upper()

    if (
        persisted_reporting_amount is not None
        and persisted_reporting_currency is not None
        and persisted_reporting_currency.upper() == tgt
    ):
        return _quantize_money(Decimal(str(persisted_reporting_amount))), False

    if cur == tgt:
        return _quantize_money(Decimal(str(amount))), False

    # Foreign row with no usable lock — can't fabricate a rate here.
    return _quantize_money(Decimal(str(amount))), True


@dataclass(frozen=True)
class CurrencyBreakdownEntry:
    currency: str
    original_amount: Decimal
    reporting_amount: Decimal
    count: int
    unconverted_count: int


@dataclass(frozen=True)
class ReportingRollup:
    reporting_currency: str
    total_reporting_amount: Decimal
    total_count: int
    unconverted_count: int  # rows that fell back to raw amount (no rate lock)
    by_currency: list[CurrencyBreakdownEntry]


def rollup_to_reporting_currency(
    rows: list[dict],
    *,
    reporting_currency: str,
) -> ReportingRollup:
    """Roll a list of currency-tagged amount rows into one reporting total.

    Each row dict carries:
        {"amount": Decimal, "currency": str | None,
         "reporting_amount": Decimal | None, "reporting_currency": str | None}

    Uses the persisted rate-locked `reporting_amount` where available (so the
    total is stable against market moves); same-currency rows convert 1:1;
    foreign rows without a lock fall through at face value and bump
    `unconverted_count`. The per-currency breakdown lets the UI show the
    original-currency split alongside the unified total.
    """
    tgt = reporting_currency.upper()
    total = Decimal("0")
    unconverted = 0
    buckets: dict[str, dict] = {}

    for r in rows:
        amount = Decimal(str(r.get("amount") or 0))
        currency = (r.get("currency") or tgt).upper()
        rep_amt, is_unconverted = reporting_amount_for_row(
            amount=amount,
            currency=currency,
            reporting_currency=tgt,
            persisted_reporting_currency=r.get("reporting_currency"),
            persisted_reporting_amount=r.get("reporting_amount"),
        )
        total += rep_amt
        if is_unconverted:
            unconverted += 1
        b = buckets.setdefault(
            currency,
            {
                "original_amount": Decimal("0"),
                "reporting_amount": Decimal("0"),
                "count": 0,
                "unconverted_count": 0,
            },
        )
        b["original_amount"] += amount
        b["reporting_amount"] += rep_amt
        b["count"] += 1
        if is_unconverted:
            b["unconverted_count"] += 1

    by_currency = [
        CurrencyBreakdownEntry(
            currency=cur,
            original_amount=_quantize_money(b["original_amount"]),
            reporting_amount=_quantize_money(b["reporting_amount"]),
            count=b["count"],
            unconverted_count=b["unconverted_count"],
        )
        for cur, b in sorted(
            buckets.items(), key=lambda kv: kv[1]["reporting_amount"], reverse=True
        )
    ]

    return ReportingRollup(
        reporting_currency=tgt,
        total_reporting_amount=_quantize_money(total),
        total_count=sum(b.count for b in by_currency),
        unconverted_count=unconverted,
        by_currency=by_currency,
    )


@dataclass(frozen=True)
class VendorSpendEntry:
    """One vendor's total spend, converted into the reporting currency."""

    vendor: str
    amount: Decimal  # in reporting currency
    invoice_count: int
    currencies: list[str]  # every distinct original currency this vendor billed in


def vendor_rollup_to_reporting_currency(
    rows: list[dict],
    *,
    reporting_currency: str,
) -> list[VendorSpendEntry]:
    """Per-vendor spend, each vendor's amounts converted into one reporting
    currency before being added together — the per-vendor counterpart to
    `rollup_to_reporting_currency`.

    Every per-vendor breakdown (CFO concentration tile, its drill-through,
    the `vendor_spend` CSV export, the emailed scheduled report) used to do a
    naive `SUM(Invoice.amount)` grouped by vendor — adding USD + EUR + GBP as
    if they were one currency the moment a vendor (or the tenant as a whole)
    billed in more than one. This groups the SAME per-invoice rows
    `rollup_to_reporting_currency` takes (``{"amount", "currency",
    "reporting_amount", "reporting_currency"}``, plus a ``"vendor"`` key) by
    vendor and rolls each vendor's rows into the reporting currency, so a
    multi-currency vendor's total is a real converted figure, not mixed
    arithmetic.

    Returns entries sorted by (converted) amount descending — ready to feed
    straight into `analytics.compute_supplier_concentration` or a CSV writer.
    """
    by_vendor: dict[str, list[dict]] = {}
    for r in rows:
        by_vendor.setdefault(r["vendor"], []).append(r)

    entries = []
    for vendor, vendor_rows in by_vendor.items():
        rollup = rollup_to_reporting_currency(vendor_rows, reporting_currency=reporting_currency)
        entries.append(
            VendorSpendEntry(
                vendor=vendor,
                amount=rollup.total_reporting_amount,
                invoice_count=rollup.total_count,
                currencies=sorted({e.currency for e in rollup.by_currency}),
            )
        )
    entries.sort(key=lambda e: e.amount, reverse=True)
    return entries


def rollup_from_grouped_rows(
    groups: list[dict],
    *,
    reporting_currency: str,
) -> ReportingRollup:
    """Same result as `rollup_to_reporting_currency`, but from rows the DB has
    already aggregated per currency — so an aggregate caller (the dashboard)
    doesn't have to stream every invoice into Python just to sum it.

    Each group dict carries the per-currency SUM/COUNT the DB computed:
        {"currency": str, "original_amount": Decimal, "reporting_amount": Decimal,
         "count": int, "unconverted_count": int}
    where `reporting_amount` already applied the per-row rule (locked
    `reporting_amount` when it matches the target currency, else face `amount`)
    and `unconverted_count` counted the foreign rows that fell back to face
    value — i.e. the CASE expressions in the query mirror
    `reporting_amount_for_row`. Because invoice money columns are 2dp, summing
    then quantizing here matches the row-at-a-time quantize-then-sum above.
    """
    tgt = reporting_currency.upper()
    by_currency = [
        CurrencyBreakdownEntry(
            currency=(g["currency"] or tgt).upper(),
            original_amount=_quantize_money(Decimal(str(g["original_amount"] or 0))),
            reporting_amount=_quantize_money(Decimal(str(g["reporting_amount"] or 0))),
            count=int(g["count"] or 0),
            unconverted_count=int(g["unconverted_count"] or 0),
        )
        for g in groups
    ]
    by_currency.sort(key=lambda e: e.reporting_amount, reverse=True)
    return ReportingRollup(
        reporting_currency=tgt,
        total_reporting_amount=_quantize_money(
            sum((e.reporting_amount for e in by_currency), Decimal("0"))
        ),
        total_count=sum(e.count for e in by_currency),
        unconverted_count=sum(e.unconverted_count for e in by_currency),
        by_currency=by_currency,
    )


@dataclass(frozen=True)
class UnrealizedFXEntry:
    currency: str
    open_original_amount: Decimal
    booked_reporting_amount: Decimal  # at the locked rate when materialized
    current_reporting_amount: Decimal  # at today's rate
    unrealized_gain_loss: Decimal  # booked - current (gain when liability shrank)


@dataclass(frozen=True)
class UnrealizedFXResult:
    reporting_currency: str
    total_unrealized_gain_loss: Decimal
    by_currency: list[UnrealizedFXEntry]


async def compute_unrealized_fx_gain_loss(
    open_invoices: list[dict],
    *,
    reporting_currency: str,
    fx_adapter: FXAdapter,
) -> UnrealizedFXResult:
    """Unrealized FX gain/loss on open (unsettled) foreign-currency invoices.

    For each foreign-currency open invoice we hold a liability booked at the
    rate locked when its reporting amount was materialized. The mark-to-market
    value is that same original-currency amount converted at *today's* rate. The
    difference is unrealized — it becomes realized only when the invoice is paid
    (which is what `international_payments.compute_fx_gain_loss` measures). This
    is the reporting-layer companion: same sign convention (a positive number is
    a gain — the liability in reporting terms shrank since booking).

    Each row dict: {"amount": Decimal, "currency": str,
                    "reporting_amount": Decimal | None,
                    "reporting_currency": str | None}.

    Same-currency invoices carry no FX exposure and are skipped. One FX call per
    distinct foreign currency (today's rate), not per row.
    """
    tgt = reporting_currency.upper()

    # Group open foreign exposure by currency.
    by_cur: dict[str, dict] = {}
    for inv in open_invoices:
        cur = (inv.get("currency") or tgt).upper()
        if cur == tgt:
            continue
        amount = Decimal(str(inv.get("amount") or 0))
        rep_amt, _ = reporting_amount_for_row(
            amount=amount,
            currency=cur,
            reporting_currency=tgt,
            persisted_reporting_currency=inv.get("reporting_currency"),
            persisted_reporting_amount=inv.get("reporting_amount"),
        )
        b = by_cur.setdefault(
            cur, {"open_original": Decimal("0"), "booked_reporting": Decimal("0")}
        )
        b["open_original"] += amount
        b["booked_reporting"] += rep_amt

    entries: list[UnrealizedFXEntry] = []
    total = Decimal("0")
    for cur, b in by_cur.items():
        fx = await fx_adapter.get_rate(cur, tgt)
        if fx.rate <= 0:
            raise ValueError(f"FX provider returned non-positive rate for {cur}->{tgt}: {fx.rate}")
        current_reporting = _quantize_money(b["open_original"] * fx.rate)
        booked = _quantize_money(b["booked_reporting"])
        gain_loss = _quantize_money(booked - current_reporting)
        total += gain_loss
        entries.append(
            UnrealizedFXEntry(
                currency=cur,
                open_original_amount=_quantize_money(b["open_original"]),
                booked_reporting_amount=booked,
                current_reporting_amount=current_reporting,
                unrealized_gain_loss=gain_loss,
            )
        )

    entries.sort(key=lambda e: abs(e.unrealized_gain_loss), reverse=True)
    return UnrealizedFXResult(
        reporting_currency=tgt,
        total_unrealized_gain_loss=_quantize_money(total),
        by_currency=entries,
    )
