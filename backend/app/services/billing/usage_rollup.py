"""Usage rollup — aggregate metered activity into billable meters per org/period.

Reads the existing per-TENANT usage tables (`extraction_usage` / `card_rebates`
live in each tenant DB, not the control plane) and folds them into a small,
stable ``UsageRollup`` the billing surfaces (the customer subscription endpoint,
the billing adapter's ``report_usage``) consume. It is **read-only and pure of
side effects** — it never mutates and never moves money.

Money invariant: every monetary amount returned is an exact ``Decimal``
(``card_rebates.amount`` is ``Numeric``); counts are ints. No floats anywhere.

Meters in this first slice:
  * ``extractions`` — count of ``extraction_usage`` rows in the period (the
    primary usage driver; ``program_type='platform'`` rows are the billable
    ones, but the count is exposed wholesale and a ``platform`` breakdown too).
  * ``card_rebate_total`` — sum of ``card_rebates.amount`` in the period
    (informational — rebates accrue to the customer; surfaced so the billing
    statement can net them later, NOT billed in this slice).

Later slices add payment-volume meters + per-meter overage pricing using the
``Plan.usage_components`` decimal-string config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage import ExtractionUsage
from app.models.virtual_card import CardRebate


@dataclass(frozen=True)
class UsageRollup:
    """Billable meters for one org over one period (``YYYY-MM``)."""

    organization_id: str
    period: str
    extractions: int = 0
    extractions_platform: int = 0
    card_rebate_total: Decimal = field(default_factory=lambda: Decimal("0.00"))

    def as_meters(self) -> dict[str, str]:
        """Serialize for an API/adapter payload — money as exact decimal strings,
        counts as strings too (a single stable string-typed meter map)."""
        return {
            "extractions": str(self.extractions),
            "extractions_platform": str(self.extractions_platform),
            "card_rebate_total": str(self.card_rebate_total),
        }


async def rollup_usage(
    db: AsyncSession,
    *,
    organization_id,
    period: str,
) -> UsageRollup:
    """Aggregate the org's metered activity for ``period`` (``YYYY-MM``).

    ``db`` is a TENANT session (where ``extraction_usage`` and ``card_rebates``
    live — both are per-tenant tables). Returns a zero-filled rollup when there's
    no activity — never ``None`` — so callers don't special-case empty months.
    """
    extractions_total = (
        await db.execute(
            select(func.count())
            .select_from(ExtractionUsage)
            .where(
                ExtractionUsage.organization_id == organization_id,
                ExtractionUsage.period == period,
            )
        )
    ).scalar_one()

    extractions_platform = (
        await db.execute(
            select(func.count())
            .select_from(ExtractionUsage)
            .where(
                ExtractionUsage.organization_id == organization_id,
                ExtractionUsage.period == period,
                ExtractionUsage.program_type == "platform",
            )
        )
    ).scalar_one()

    # COALESCE so an org with no rebates this period yields Decimal('0.00'),
    # not None. Numeric column → SQLAlchemy returns Decimal (never float).
    rebate_total = (
        await db.execute(
            select(func.coalesce(func.sum(CardRebate.amount), Decimal("0.00"))).where(
                CardRebate.organization_id == organization_id,
                CardRebate.period == period,
            )
        )
    ).scalar_one()

    return UsageRollup(
        organization_id=str(organization_id),
        period=period,
        extractions=int(extractions_total or 0),
        extractions_platform=int(extractions_platform or 0),
        card_rebate_total=Decimal(rebate_total),
    )
