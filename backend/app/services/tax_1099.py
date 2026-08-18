"""1099 reporting + vendor tax bookkeeping.

US AP table stakes: track which vendors crossed the $600/year threshold,
whether a W-9 is on file, and what tax classification they reported. The
report endpoint is pure-query so it can be run ad-hoc during January
close without side effects.

This module is the aggregation layer. Form *generation* lives in
``tax_1099_forms`` (PDF), and *e-filing* lives in the
``tax_filing_adapters`` package + the ``POST /api/tax/1099/file``
endpoint — both reuse the rows this module computes. See
``backend/docs/tax-1099.md``.

Public API:
    - ``build_1099_report(db, organization_id, year)`` — compute the list
    - ``build_1099_dashboard(db, organization_id, year)`` — readiness view
    - ``THRESHOLD_USD`` — $600, the IRS filing threshold
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.vendor import Vendor
from app.services.currency_conversion import payment_reporting_amount_sql
from app.services.payment_methods import card_payment_method_clause

# IRS 1099-NEC / 1099-MISC reporting threshold for the 2024+ tax years.
# Lowered from $600 to $5000 for 1099-K specifically, but 1099-NEC
# (contractor payments) remains $600 — that's the common AP case.
THRESHOLD_USD = Decimal("600")


@dataclass
class VendorReportRow:
    vendor_id: uuid.UUID
    vendor_name: str
    tax_id: str | None
    tax_classification: str | None
    is_1099_eligible: bool
    w9_received_date: date | None
    w9_on_file: bool
    # REPORTABLE year-to-date paid — card-rail payments are excluded (they are
    # the card settlement entity's 1099-K, not our 1099). This is the figure
    # that lands in the 1099 box amount.
    ytd_paid: Decimal
    over_threshold: bool
    payment_count: int
    # True once a TIN match has stamped ``Vendor.tin_verified_at``. Defaulted
    # so older call sites that build rows by hand keep working.
    tin_verified: bool = False
    # The card-rail total deliberately EXCLUDED from ``ytd_paid``, surfaced so
    # an operator can reconcile against the processor's 1099-K instead of the
    # money silently vanishing from the report. Defaulted for the same reason
    # as ``tin_verified``.
    card_paid: Decimal = Decimal("0")
    card_payment_count: int = 0
    # Completed payments whose outflow could not be expressed in the reporting
    # currency at all (see ``currency_conversion.payment_reporting_amount_sql``).
    # A COUNT and not a total, deliberately: summing figures across unknown
    # currencies is exactly the mixture being refused. Non-zero means this
    # vendor's box amount is UNDERSTATED and a human has to establish the
    # home-currency figure before filing.
    unconverted_payment_count: int = 0

    def to_dict(self) -> dict:
        return {
            "vendor_id": str(self.vendor_id),
            "vendor_name": self.vendor_name,
            "tax_id": self.tax_id,
            "tax_classification": self.tax_classification,
            "is_1099_eligible": self.is_1099_eligible,
            "w9_received_date": self.w9_received_date.isoformat()
            if self.w9_received_date
            else None,
            "w9_on_file": self.w9_on_file,
            "ytd_paid": str(self.ytd_paid),
            "over_threshold": self.over_threshold,
            "payment_count": self.payment_count,
            "tin_verified": self.tin_verified,
            "card_paid": str(self.card_paid),
            "card_payment_count": self.card_payment_count,
            "unconverted_payment_count": self.unconverted_payment_count,
        }


def _total_unconverted_payments(rows: list[VendorReportRow]) -> int:
    """Completed payments across every vendor row that could not be expressed
    in the reporting currency, and so are missing from ``total_reportable``.

    Surfaced alongside the totals for the same reason ``total_card_excluded``
    is: money that has been deliberately left out of a filed figure must be
    visible and reconcilable, never silently absent. Non-zero means the report
    is not yet filable as it stands."""
    return sum(r.unconverted_payment_count for r in rows)


def _total_card_excluded(rows: list[VendorReportRow]) -> str:
    """Card-rail spend for the year across EVERY vendor row — the money the
    1099 deliberately leaves out because the card settlement entity reports it
    on a 1099-K. Spans all vendors (not just the eligible-over-threshold ones
    ``total_reportable`` covers) because it exists to be reconciled against the
    processor's own filing, which knows nothing about our eligibility flags."""
    return str(sum((r.card_paid for r in rows), Decimal("0")))


@dataclass
class Report1099:
    year: int
    generated_at: date
    rows: list[VendorReportRow]
    threshold_usd: Decimal = THRESHOLD_USD
    # The currency the reportable totals + per-vendor ``ytd_paid`` are actually
    # denominated in — the org's reporting (home) currency. Not a label applied
    # after the fact: the aggregation only counts a payment it can PROVE is
    # denominated in this currency (``currency_conversion.payment_reporting_amount_sql``),
    # and counts the rest on ``unconverted_payment_count`` instead. 1099 is a
    # US/IRS concept (dollars), but a non-USD tenant's home currency is
    # surfaced honestly here instead of being silently called "USD".
    currency: str = "USD"

    def summary(self) -> dict:
        eligible_over = [r for r in self.rows if r.is_1099_eligible and r.over_threshold]
        total_reportable = str(sum((r.ytd_paid for r in eligible_over), Decimal("0")))
        return {
            "year": self.year,
            "threshold_usd": str(self.threshold_usd),
            "currency": self.currency,
            "vendor_count_total": len(self.rows),
            "vendor_count_eligible_over_threshold": len(eligible_over),
            "vendor_count_over_threshold_without_w9": sum(
                1 for r in eligible_over if not r.w9_on_file
            ),
            "total_reportable": total_reportable,
            # Back-compat alias of ``total_reportable`` — historically named
            # ``_usd`` before the currency became explicit. Same value; kept so
            # existing API consumers don't break. Prefer ``total_reportable`` +
            # ``currency``.
            "total_reportable_usd": total_reportable,
            "total_card_excluded": _total_card_excluded(self.rows),
            "unconverted_payment_count": _total_unconverted_payments(self.rows),
        }

    def to_dict(self) -> dict:
        return {
            **self.summary(),
            "generated_at": self.generated_at.isoformat(),
            "rows": [r.to_dict() for r in self.rows],
        }


async def build_1099_report(
    db: AsyncSession,
    organization_id: uuid.UUID,
    year: int,
    reporting_currency: str = "USD",
) -> Report1099:
    """Aggregate completed payments per vendor for the given calendar year.

    Only payments with ``status='completed'`` and a ``completed_at`` in
    the target year are counted — pending/failed payments don't show up
    on a 1099. ``completed_at`` is preferred over the invoice date
    because the IRS reports payments in the year they were actually made.

    **Card-rail payments are excluded from ``ytd_paid``** and totalled
    separately on ``card_paid``: the card settlement entity reports those on a
    Form 1099-K, so putting them in our 1099 box amount over-reports the
    vendor and double-counts the same dollar. The classification lives in
    ``services/payment_methods`` — see that module for the rail-by-rail
    treatment and the drift guard.

    ``reporting_currency`` is the org's reporting (home) currency, resolved by
    the caller via ``currency_conversion.resolve_reporting_currency``. It is
    **not** a label applied to whatever the SUM produced: ``Payment.amount`` is
    denominated in the INVOICE's currency (see
    ``international_payments.prepare_international_payment``), so a book with
    one EUR invoice in it used to add 1 000 EUR into a USD box amount at face
    value. ``currency_conversion.payment_reporting_amount_sql`` resolves each
    payment's outflow into ``reporting_currency`` — the rate-locked
    ``source_amount`` when the payment carries a home-currency leg, otherwise
    ``amount`` when the invoice is already in that currency — and a payment
    neither rung can establish is left OUT of ``ytd_paid`` / ``card_paid`` and
    counted on ``unconverted_payment_count`` instead. Nothing is converted at
    read time; a rate fetched on a read would make a filed historical total
    move under the reader.
    """
    # Conditional aggregation splits the joined payments in one pass. ``case``
    # with no ``else_`` yields NULL on the other branch, which ``sum``/``count``
    # skip — so the Decimal("0") coalesce fallback (never int 0, which can
    # promote the aggregate off Numeric and mis-classify a vendor sitting
    # exactly at the $600 threshold) still governs the empty case.
    is_card = card_payment_method_clause(Payment.method)
    reported = payment_reporting_amount_sql(
        reporting_currency=reporting_currency,
        payment_amount=Payment.amount,
        payment_source_amount=Payment.source_amount,
        payment_source_currency=Payment.source_currency,
        invoice_currency=Invoice.currency,
    )
    countable = reported.is_expressible
    q = (
        select(
            Vendor.id.label("vendor_id"),
            Vendor.name.label("vendor_name"),
            Vendor.tax_id.label("tax_id"),
            Vendor.tax_classification.label("tax_classification"),
            Vendor.is_1099_eligible.label("is_1099_eligible"),
            Vendor.w9_received_date.label("w9_received_date"),
            Vendor.w9_file_key.label("w9_file_key"),
            Vendor.tin_verified_at.label("tin_verified_at"),
            func.coalesce(
                func.sum(case((and_(~is_card, countable), reported.amount))), Decimal("0")
            ).label("ytd_paid"),
            func.count(case((and_(~is_card, countable), Payment.id))).label("payment_count"),
            func.coalesce(
                func.sum(case((and_(is_card, countable), reported.amount))), Decimal("0")
            ).label("card_paid"),
            func.count(case((and_(is_card, countable), Payment.id))).label("card_payment_count"),
            func.count(case((~countable, Payment.id))).label("unconverted_payment_count"),
        )
        .select_from(Vendor)
        .outerjoin(Invoice, Invoice.vendor_id == Vendor.id)
        .outerjoin(
            Payment,
            (Payment.invoice_id == Invoice.id)
            & (Payment.status == "completed")
            & (extract("year", Payment.completed_at) == year),
        )
        .where(Vendor.organization_id == organization_id)
        .group_by(
            Vendor.id,
            Vendor.name,
            Vendor.tax_id,
            Vendor.tax_classification,
            Vendor.is_1099_eligible,
            Vendor.w9_received_date,
            Vendor.w9_file_key,
            Vendor.tin_verified_at,
        )
        .order_by(Vendor.name)
    )
    result = await db.execute(q)
    rows = []
    for row in result.all():
        ytd = Decimal(row.ytd_paid or Decimal("0"))
        rows.append(
            VendorReportRow(
                vendor_id=row.vendor_id,
                vendor_name=row.vendor_name,
                tax_id=row.tax_id,
                tax_classification=row.tax_classification,
                is_1099_eligible=bool(row.is_1099_eligible),
                w9_received_date=row.w9_received_date,
                w9_on_file=row.w9_file_key is not None,
                ytd_paid=ytd,
                over_threshold=ytd >= THRESHOLD_USD,
                payment_count=int(row.payment_count or 0),
                tin_verified=row.tin_verified_at is not None,
                card_paid=Decimal(row.card_paid or Decimal("0")),
                card_payment_count=int(row.card_payment_count or 0),
                unconverted_payment_count=int(row.unconverted_payment_count or 0),
            )
        )

    return Report1099(
        year=year,
        generated_at=date.today(),
        rows=rows,
        currency=(reporting_currency or "USD").upper(),
    )


# ---------------------------------------------------------------------------
# 1099 vendor dashboard
# ---------------------------------------------------------------------------


def _row_needs_attention(row: VendorReportRow) -> bool:
    """A 1099-eligible vendor that can't be cleanly filed as things stand.

    Two ways in:

    * over the $600 threshold and missing a W-9 on file or a verified TIN, or
    * holding a completed payment whose outflow could not be expressed in the
      reporting currency (``unconverted_payment_count``), which means the box
      amount on record is UNDERSTATED by that payment. That one is flagged
      regardless of the threshold, precisely because the missing money is what
      could carry the vendor over it.
    """
    if row.is_1099_eligible and row.unconverted_payment_count:
        return True
    return (
        row.is_1099_eligible and row.over_threshold and (not row.w9_on_file or not row.tin_verified)
    )


@dataclass
class Dashboard1099:
    """A compliance-readiness view over the 1099 report rows.

    Same underlying aggregation as ``build_1099_report``; this adds the
    W-9 / TIN-verified / threshold readiness flags an AP team needs to know
    who still needs chasing before filing season.
    """

    year: int
    generated_at: date
    rows: list[VendorReportRow]
    threshold_usd: Decimal = THRESHOLD_USD
    # See ``Report1099.currency`` — the reporting (home) currency the totals are
    # denominated in, enforced by the aggregation rather than asserted.
    currency: str = "USD"

    def summary(self) -> dict:
        eligible = [r for r in self.rows if r.is_1099_eligible]
        eligible_over = [r for r in eligible if r.over_threshold]
        total_reportable = str(sum((r.ytd_paid for r in eligible_over), Decimal("0")))
        return {
            "year": self.year,
            "threshold_usd": str(self.threshold_usd),
            "currency": self.currency,
            "vendor_count_total": len(self.rows),
            "vendor_count_eligible": len(eligible),
            "vendor_count_eligible_over_threshold": len(eligible_over),
            "vendor_count_over_threshold_without_w9": sum(
                1 for r in eligible_over if not r.w9_on_file
            ),
            "vendor_count_over_threshold_tin_unverified": sum(
                1 for r in eligible_over if not r.tin_verified
            ),
            "vendor_count_needs_attention": sum(1 for r in self.rows if _row_needs_attention(r)),
            "total_reportable": total_reportable,
            # Back-compat alias — see ``Report1099.summary``.
            "total_reportable_usd": total_reportable,
            "total_card_excluded": _total_card_excluded(self.rows),
            "unconverted_payment_count": _total_unconverted_payments(self.rows),
        }

    def to_dict(self) -> dict:
        return {
            **self.summary(),
            "generated_at": self.generated_at.isoformat(),
            "rows": [
                {**r.to_dict(), "needs_attention": _row_needs_attention(r)} for r in self.rows
            ],
        }


async def build_1099_dashboard(
    db: AsyncSession,
    organization_id: uuid.UUID,
    year: int,
    reporting_currency: str = "USD",
) -> Dashboard1099:
    """Build the 1099-eligible vendor dashboard for a year.

    Reuses ``build_1099_report``'s aggregation and re-frames it around filing
    readiness (W-9-on-file, TIN-verified, threshold, needs-attention)."""
    report = await build_1099_report(db, organization_id, year, reporting_currency)
    return Dashboard1099(
        year=report.year,
        generated_at=report.generated_at,
        rows=report.rows,
        currency=report.currency,
    )
