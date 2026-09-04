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
  * ``card_rebate_totals`` — rebate amounts in the period, **grouped by the
    currency they are denominated in** (informational — rebates accrue to the
    customer; surfaced so the billing statement can net them later, NOT billed
    in this slice). It was a single cross-currency ``sum(card_rebates.amount)``,
    which is a quantity in no currency at all — and this is a meter a later
    slice prices, so a mixed scalar could not be turned into a charge without
    inventing a rate. ``card_rebates`` carries no currency column, so a
    rebate's denomination is only knowable through its card; that is why this
    joins ``virtual_cards`` rather than summing one table.

    Deliberately **org-wide**, not entity-scoped: the platform bills the
    customer ORG, so a subsidiary breakdown would be the wrong unit here. (The
    sibling figure on ``GET /api/payments/summary`` IS entity-scoped, because
    that one sits beside entity-scoped outflows an operator reconciles it
    against — same table, different question.)

Later slices add payment-volume meters + per-meter overage pricing using the
``Plan.usage_components`` decimal-string config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.usage import ExtractionUsage
from app.models.virtual_card import CardRebate, VirtualCard


@dataclass(frozen=True)
class CurrencyTotal:
    """One money total and the currency it is actually denominated in.

    A money figure with no currency beside it cannot be priced, netted or
    compared — which is the whole reason the rebate meter is grouped rather
    than summed.
    """

    currency: str
    amount: Decimal


@dataclass(frozen=True)
class UsageRollup:
    """Billable meters for one org over one period (``YYYY-MM``)."""

    organization_id: str
    period: str
    extractions: int = 0
    extractions_platform: int = 0
    #: Sorted by currency code, so the meter map's key order is stable across
    #: calls (a provider diffing meter events should not see churn from
    #: Postgres' grouping order).
    card_rebate_totals: tuple[CurrencyTotal, ...] = field(default_factory=tuple)

    def as_meters(self) -> dict[str, str]:
        """Serialize for an API/adapter payload — money as exact decimal strings,
        counts as strings too (a single stable string-typed meter map).

        The rebate meter is emitted **one key per currency**
        (``card_rebate_total.USD``), always — never a bare
        ``card_rebate_total``. One shape rather than two: a single-currency org
        would otherwise get a differently-named meter from a multi-currency
        one, and a consumer would have to know which it was looking at. The
        currency being IN the key is also what makes the meter priceable.
        ``report_usage`` iterates the map generically, so no adapter changes.
        An org with no rebates emits no rebate key at all, which is honest —
        zero rebates in an unstated currency is not a fact.
        """
        meters = {
            "extractions": str(self.extractions),
            "extractions_platform": str(self.extractions_platform),
        }
        for total in self.card_rebate_totals:
            meters[f"card_rebate_total.{total.currency}"] = str(total.amount)
        return meters


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

    # Grouped by the card's currency — `card_rebates` has no currency column of
    # its own, so the join is the only way to know what these amounts are
    # denominated in. A card with no currency stamped (pre-multi-currency row)
    # coalesces to the platform default rather than being dropped from the
    # meter entirely; losing money from a billing figure is worse than
    # attributing it to the default, and it is visible either way because the
    # currency is in the key.
    rebate_currency = func.upper(
        func.coalesce(VirtualCard.currency, settings.reporting_currency_default)
    )
    rebate_rows = (
        await db.execute(
            select(
                rebate_currency.label("currency"),
                func.coalesce(func.sum(CardRebate.amount), Decimal("0.00")),
            )
            .select_from(CardRebate)
            .join(VirtualCard, VirtualCard.id == CardRebate.virtual_card_id)
            .where(
                CardRebate.organization_id == organization_id,
                CardRebate.period == period,
            )
            .group_by(rebate_currency)
            .order_by(rebate_currency)
        )
    ).all()

    return UsageRollup(
        organization_id=str(organization_id),
        period=period,
        card_rebate_totals=tuple(
            CurrencyTotal(currency=str(cur), amount=Decimal(str(amt or 0)))
            for cur, amt in rebate_rows
        ),
        extractions=int(extractions_total or 0),
        extractions_platform=int(extractions_platform or 0),
    )
