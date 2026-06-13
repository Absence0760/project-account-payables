"""Per-period international-tax report aggregation.

Reads persisted ``IntlTaxRecord`` rows for a tenant + date range and rolls
them into a report: VAT (output / reverse-charge / net), GST (by component),
and withholding (withheld / net paid), broken down by country. The query is
tenant-scoped — the caller passes the tenant DB session resolved via
``get_tenant_db`` so isolation is enforced at the data layer.

All sums are ``Decimal``; the function never coerces money to ``float``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.international_tax import IntlTaxRecord, TaxKind

_ZERO = Decimal("0.00")


@dataclass
class CountryTaxLine:
    """One country's tax totals within the period."""

    country_code: str
    currency: str
    vat_output: Decimal = _ZERO  # VAT charged on standard supplies
    vat_reverse_charge: Decimal = _ZERO  # reportable VAT under reverse charge
    gst_total: Decimal = _ZERO
    gst_components: dict[str, Decimal] = field(default_factory=dict)
    withholding_total: Decimal = _ZERO  # tax withheld and remitted
    record_count: int = 0


@dataclass
class TaxReport:
    """The full per-period report."""

    period_start: date
    period_end: date
    countries: list[CountryTaxLine]
    total_vat_output: Decimal
    total_vat_reverse_charge: Decimal
    total_gst: Decimal
    total_withholding: Decimal
    record_count: int


async def generate_tax_report(
    db: AsyncSession,
    *,
    period_start: date,
    period_end: date,
    country_code: str | None = None,
) -> TaxReport:
    """Aggregate ``intl_tax_records`` for the period into a ``TaxReport``.

    ``period_start`` / ``period_end`` are inclusive on ``tax_point_date``.
    Optional ``country_code`` filters to a single jurisdiction.
    """
    stmt = select(IntlTaxRecord).where(
        IntlTaxRecord.tax_point_date >= period_start,
        IntlTaxRecord.tax_point_date <= period_end,
    )
    if country_code:
        stmt = stmt.where(IntlTaxRecord.country_code == country_code.strip().upper())

    rows = (await db.execute(stmt)).scalars().all()

    by_country: dict[str, CountryTaxLine] = {}
    total_vat_output = _ZERO
    total_vat_rc = _ZERO
    total_gst = _ZERO
    total_wht = _ZERO

    for r in rows:
        line = by_country.get(r.country_code)
        if line is None:
            line = CountryTaxLine(country_code=r.country_code, currency=r.currency)
            by_country[r.country_code] = line
        line.record_count += 1

        if r.kind == TaxKind.vat:
            if r.reverse_charge:
                line.vat_reverse_charge += r.tax_amount
                total_vat_rc += r.tax_amount
            else:
                line.vat_output += r.tax_amount
                total_vat_output += r.tax_amount
        elif r.kind == TaxKind.gst:
            line.gst_total += r.tax_amount
            total_gst += r.tax_amount
            for name, amount in (r.components or {}).items():
                # Components persisted as string-Decimal in JSONB.
                acc = line.gst_components.get(name, _ZERO)
                line.gst_components[name] = acc + Decimal(str(amount))
        elif r.kind == TaxKind.withholding:
            line.withholding_total += r.tax_amount
            total_wht += r.tax_amount

    countries = sorted(by_country.values(), key=lambda c: c.country_code)
    return TaxReport(
        period_start=period_start,
        period_end=period_end,
        countries=countries,
        total_vat_output=total_vat_output,
        total_vat_reverse_charge=total_vat_rc,
        total_gst=total_gst,
        total_withholding=total_wht,
        record_count=len(rows),
    )


def summarize_records(records: list[dict]) -> dict[str, Decimal]:
    """Pure helper: roll up a list of plain record dicts into period totals.

    Used by the report-aggregation unit test (no DB needed) and by any
    caller that already has the records in memory. Each dict needs
    ``kind`` / ``tax_amount`` / ``reverse_charge`` keys.
    """
    totals: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for r in records:
        amount = Decimal(str(r["tax_amount"]))
        kind = r["kind"]
        if kind == TaxKind.vat.value or kind == TaxKind.vat:
            if r.get("reverse_charge"):
                totals["vat_reverse_charge"] += amount
            else:
                totals["vat_output"] += amount
        elif kind == TaxKind.gst.value or kind == TaxKind.gst:
            totals["gst"] += amount
        elif kind == TaxKind.withholding.value or kind == TaxKind.withholding:
            totals["withholding"] += amount
    return dict(totals)
