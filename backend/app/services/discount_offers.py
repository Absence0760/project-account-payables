"""Dynamic-discount offer logic — sliding-scale tiers + lifecycle + bulk negotiation.

A :class:`~app.models.discount.DiscountOffer` is a time-boxed early-payment
proposal, optionally a *sliding scale* of tiers (pay sooner → bigger discount):

    [{"days": 5, "percent": "3.00"}, {"days": 10, "percent": "2.00"}]

This module is the pure business logic behind that model — tier normalization,
"which tier can we still capture today", exact-cents savings math, and the
accept / decline / capture / expire transitions. It does **no** DB or network
work: the lifecycle helpers *mutate* a passed-in offer-like object inside a
session the caller (the ``/api/discounts`` router) owns and commits. ROI math
is **not** duplicated here — that lives in ``services/discount_roi.py``.

Tier semantics (the rule chosen here, applied consistently)
-----------------------------------------------------------
A tier ``{"days": N, "percent": P}`` means: *"pay within ``N`` days of the
offer's reference date (``valid_from``, falling back to the offer's
``created`` date supplied by the caller) to earn ``P`` % off."* The capture
**deadline** for that rung is ``reference_date + N days``. As of some
``as_of`` date a tier is still *achievable* when ``as_of <= reference + N``.

When several tiers are still achievable, :func:`best_tier_for_date` returns the
one with the **highest percent** (best for the buyer) — which, because percent
decreases as the window widens, is the *tightest still-open* window. The whole
offer is dead once ``valid_until`` has passed (``as_of > valid_until``):
:func:`best_tier_for_date` then returns ``None`` regardless of the tiers.

Money is ``Decimal`` and tier percents are carried as Decimal-strings (never
float) to match the JSONB storage on the model. See
``backend/docs/dynamic-discounting.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.models.discount import (
    OFFER_SCOPE_VENDOR,
    OFFER_SOURCE_SUPPLIER,
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_CAPTURED,
    OFFER_STATUS_DECLINED,
    OFFER_STATUS_EXPIRED,
    OFFER_STATUS_OFFERED,
)

_CENTS = Decimal("0.01")
_PCT = Decimal("0.01")
_HUNDRED = Decimal("100")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# Tier parsing / normalization
# --------------------------------------------------------------------------- #


def normalize_tier(tier: dict) -> dict:
    """Coerce one tier into the canonical ``{"days": int, "percent": "X.XX"}``.

    ``percent`` is normalized to a 2-dp Decimal-**string** (JSONB has no
    Decimal). Validates ``days >= 0`` and ``0 < percent < 100``.

    Raises ``ValueError`` on missing keys or out-of-range values.
    """
    if not isinstance(tier, dict):
        raise ValueError(f"tier must be a dict, got {type(tier).__name__}")
    if "days" not in tier or "percent" not in tier:
        raise ValueError("tier must have 'days' and 'percent'")

    try:
        days = int(tier["days"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"tier days is not an integer: {tier['days']!r}") from exc
    if days < 0:
        raise ValueError(f"tier days must be >= 0, got {days}")

    try:
        percent = Decimal(str(tier["percent"]))
    except (TypeError, ArithmeticError) as exc:
        raise ValueError(f"tier percent is not a number: {tier['percent']!r}") from exc
    if not (Decimal("0") < percent < _HUNDRED):
        raise ValueError(f"tier percent must be in (0, 100), got {percent}")

    return {"days": days, "percent": str(_q_pct(percent))}


def parse_tiers(tiers: list[dict]) -> list[dict]:
    """Normalize + validate a list of tiers and sort by ``days`` ascending.

    Raises ``ValueError`` if the list is empty, malformed, or contains
    duplicate ``days`` (an ambiguous scale).
    """
    if not tiers:
        raise ValueError("at least one tier is required")
    normalized = [normalize_tier(t) for t in tiers]
    seen: set[int] = set()
    for t in normalized:
        if t["days"] in seen:
            raise ValueError(f"duplicate tier days: {t['days']}")
        seen.add(t["days"])
    normalized.sort(key=lambda t: t["days"])
    return normalized


def tier_percent(tier: dict) -> Decimal:
    """The tier's discount percent as a ``Decimal`` (parsing the stored string)."""
    return Decimal(str(tier["percent"]))


# --------------------------------------------------------------------------- #
# Tier selection
# --------------------------------------------------------------------------- #


def select_tier(tiers: list[dict], tier_days: int) -> dict | None:
    """Return the tier whose ``days`` equals ``tier_days``, else ``None``."""
    for t in tiers:
        if int(t["days"]) == int(tier_days):
            return t
    return None


def best_tier_for_date(
    tiers: list[dict],
    as_of: date,
    valid_until: date | None,
    *,
    reference_date: date | None = None,
) -> dict | None:
    """Best (highest-percent) tier still achievable as of ``as_of``.

    See the module docstring for the full rule. A tier ``{"days": N}`` has a
    capture deadline of ``reference_date + N days``; it is achievable while
    ``as_of <= deadline``. ``reference_date`` defaults to ``as_of`` itself
    (i.e. "if I pay today, every tier whose window has not yet closed counts"),
    but a caller that knows the offer's start date should pass it so the window
    is measured from the offer, not from today.

    Returns ``None`` when the offer window has passed (``as_of > valid_until``)
    or no tier is still achievable.
    """
    if valid_until is not None and as_of > valid_until:
        return None
    ref = reference_date if reference_date is not None else as_of

    best: dict | None = None
    best_pct: Decimal | None = None
    for t in tiers:
        days = int(t["days"])
        deadline = _add_days(ref, days)
        if as_of > deadline:
            continue  # this rung's window has already closed
        pct = tier_percent(t)
        if best_pct is None or pct > best_pct:
            best, best_pct = t, pct
    return best


def _add_days(d: date, days: int):
    from datetime import timedelta

    return d + timedelta(days=days)


# --------------------------------------------------------------------------- #
# Savings math
# --------------------------------------------------------------------------- #


def discount_savings(base_amount: Decimal, tier: dict) -> Decimal:
    """Dollar discount for ``tier`` against ``base_amount`` — quantized to cents.

    ``base_amount * percent / 100``, rounded half-up to two decimal places.
    """
    base = Decimal(base_amount)
    return _q_money(base * tier_percent(tier) / _HUNDRED)


# --------------------------------------------------------------------------- #
# Lifecycle transitions — MUTATE the passed-in offer; never commit.
# --------------------------------------------------------------------------- #


def accept_offer(offer, *, tier: dict, actor_id, now: datetime) -> None:
    """Transition ``offered`` → ``accepted``, recording the chosen tier.

    Stores a normalized copy of ``tier`` on ``accepted_tier`` (Decimal-string
    percent). Raises ``ValueError`` if the offer is not currently ``offered``.
    """
    if offer.status != OFFER_STATUS_OFFERED:
        raise ValueError(f"cannot accept an offer in status {offer.status!r} (must be 'offered')")
    offer.accepted_tier = normalize_tier(tier)
    offer.accepted_at = now
    offer.accepted_by = actor_id
    offer.status = OFFER_STATUS_ACCEPTED


def decline_offer(offer, *, now: datetime) -> None:
    """Transition ``offered`` → ``declined``. Raises if not ``offered``."""
    if offer.status != OFFER_STATUS_OFFERED:
        raise ValueError(f"cannot decline an offer in status {offer.status!r} (must be 'offered')")
    offer.status = OFFER_STATUS_DECLINED


def mark_captured(offer, *, captured_amount: Decimal, now: datetime) -> None:
    """Transition ``accepted`` → ``captured``, recording the realized discount.

    Raises ``ValueError`` if the offer is not currently ``accepted``.
    """
    if offer.status != OFFER_STATUS_ACCEPTED:
        raise ValueError(f"cannot capture an offer in status {offer.status!r} (must be 'accepted')")
    offer.captured_amount = _q_money(Decimal(captured_amount))
    offer.captured_at = now
    offer.status = OFFER_STATUS_CAPTURED


def expire_if_past(offer, *, as_of: date) -> bool:
    """Expire an ``offered`` offer whose ``valid_until`` is before ``as_of``.

    Returns ``True`` if the status changed, ``False`` otherwise (already
    accepted/captured/declined/expired, no ``valid_until``, or still in window).
    """
    if offer.status != OFFER_STATUS_OFFERED:
        return False
    if offer.valid_until is None or offer.valid_until >= as_of:
        return False
    offer.status = OFFER_STATUS_EXPIRED
    return True


# --------------------------------------------------------------------------- #
# Bulk vendor negotiation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BulkNegotiation:
    """The constructor kwargs for a vendor-scoped bulk :class:`DiscountOffer`.

    Pure result of summing a vendor's open-invoice balances against a proposed
    tier scale — the router turns this into the ORM row (it owns the session,
    the ``organization_id`` / ``vendor_id`` UUIDs, and the commit).
    """

    scope: str
    source: str
    vendor_id: object
    base_amount: Decimal
    tiers: list[dict]
    valid_until: date | None
    invoice_count: int
    notes: str | None = None

    def as_offer_kwargs(self) -> dict:
        """Kwargs ready to splat into ``DiscountOffer(**kwargs)``."""
        return {
            "scope": self.scope,
            "source": self.source,
            "vendor_id": self.vendor_id,
            "base_amount": self.base_amount,
            "tiers": self.tiers,
            "valid_until": self.valid_until,
            "notes": self.notes,
        }


def build_bulk_offer(
    *,
    vendor_id,
    open_amounts: list[Decimal],
    tiers: list[dict],
    valid_until: date | None = None,
    notes: str | None = None,
) -> BulkNegotiation:
    """Build a vendor-scoped bulk negotiation from a vendor's open balances.

    Sums ``open_amounts`` (the open-invoice balances) into ``base_amount`` and
    normalizes the proposed ``tiers``. Pure — returns a :class:`BulkNegotiation`
    dataclass; does **not** create the ORM row.

    Raises ``ValueError`` if there are no open amounts or the summed base is
    not positive (nothing to discount).
    """
    if not open_amounts:
        raise ValueError("a bulk offer needs at least one open invoice amount")
    base_amount = _q_money(sum((Decimal(a) for a in open_amounts), Decimal("0")))
    if base_amount <= 0:
        raise ValueError(f"bulk offer base amount must be positive, got {base_amount}")
    return BulkNegotiation(
        scope=OFFER_SCOPE_VENDOR,
        source=OFFER_SOURCE_SUPPLIER,
        vendor_id=vendor_id,
        base_amount=base_amount,
        tiers=parse_tiers(tiers),
        valid_until=valid_until,
        invoice_count=len(open_amounts),
        notes=notes,
    )
