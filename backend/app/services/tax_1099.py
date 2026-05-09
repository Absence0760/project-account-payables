"""1099 reporting + vendor tax bookkeeping.

US AP table stakes: track which vendors crossed the $600/year threshold,
whether a W-9 is on file, and what tax classification they reported. The
report endpoint is pure-query so it can be run ad-hoc during January
close without side effects.

E-filing (submitting 1099-NEC / 1099-MISC to the IRS) is intentionally
not implemented here. It's a third-party API call (Tax1099 is the
common choice) and requires the tenant to hand us the vendor's address
+ a signed 8655 authorization. See ``backend/docs/tax-1099.md`` for the
integration sketch; for pilot #1 the tenant exports the report and hand-
files or uses Tax1099's web UI directly.

Public API:
    - ``build_1099_report(db, organization_id, year)`` — compute the list
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
        }


@dataclass
class Report1099:
    year: int
    generated_at: date
    rows: list[VendorReportRow]
    threshold_usd: Decimal = THRESHOLD_USD

    def summary(self) -> dict:
        eligible_over = [r for r in self.rows if r.is_1099_eligible and r.over_threshold]
        return {
            "year": self.year,
            "threshold_usd": str(self.threshold_usd),
            "vendor_count_total": len(self.rows),
            "vendor_count_eligible_over_threshold": len(eligible_over),
            "vendor_count_over_threshold_without_w9": sum(
                1 for r in eligible_over if not r.w9_on_file
            ),
            "total_reportable_usd": str(sum((r.ytd_paid for r in eligible_over), Decimal("0"))),
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
) -> Report1099:
    """Aggregate completed payments per vendor for the given calendar year.

    Only payments with ``status='completed'`` and a ``completed_at`` in
    the target year are counted — pending/failed payments don't show up
    on a 1099. ``completed_at`` is preferred over the invoice date
    because the IRS reports payments in the year they were actually made.
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
            func.coalesce(func.sum(Payment.amount), 0).label("ytd_paid"),
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
        )
        .order_by(Vendor.name)
    )
    result = await db.execute(q)
    rows = []
    for row in result.all():
        ytd = Decimal(row.ytd_paid or 0)
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
            )
        )

    return Report1099(
        year=year,
        generated_at=date.today(),
        rows=rows,
    )
