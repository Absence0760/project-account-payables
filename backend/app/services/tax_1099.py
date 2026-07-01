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

from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.vendor import Vendor

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
    ytd_paid: Decimal
    over_threshold: bool
    payment_count: int
    # True once a TIN match has stamped ``Vendor.tin_verified_at``. Defaulted
    # so older call sites that build rows by hand keep working.
    tin_verified: bool = False

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
        }


@dataclass
class Report1099:
    year: int
    generated_at: date
    rows: list[VendorReportRow]
    threshold_usd: Decimal = THRESHOLD_USD
    # The currency the reportable totals + per-vendor ``ytd_paid`` are actually
    # denominated in — the org's reporting (home) currency. ``Payment.amount``
    # is already home-currency, so this is a LABEL, never an FX conversion. 1099
    # is a US/IRS concept (dollars), but a non-USD tenant's home currency is
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

    ``reporting_currency`` is the org's reporting (home) currency, resolved by
    the caller via ``currency_conversion.resolve_reporting_currency``. It only
    LABELS the totals — ``Payment.amount`` is already home-currency, so no FX
    conversion happens here.
    """
    # Join payment → invoice (for vendor_id) → vendor. Aggregate by vendor.
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
            # Decimal("0") fallback (not int 0): with an int the zero-payments
            # case can promote the aggregate away from Numeric, and a vendor at
            # exactly the $600 filing threshold could be mis-classified.
            func.coalesce(func.sum(Payment.amount), Decimal("0")).label("ytd_paid"),
            func.count(Payment.id).label("payment_count"),
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
    """A 1099-eligible vendor over the $600 threshold that is missing either
    a W-9 on file or a verified TIN can't be cleanly filed — surface it."""
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
    # denominated in. Label only, never an FX conversion.
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
