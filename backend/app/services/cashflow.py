"""Cash-position helpers — bank-balance auto-sync + persisted alert thresholds.

Two pieces backing the cash-position dashboard (`GET /api/analytics/cash_position`
and the threshold GET/PUT), kept out of `api/analytics.py` so the route file
stays SQL-and-shaping only:

1. ``fetch_provider_balance`` — best-effort read of the org's funding-account
   balance from its configured payment adapter's optional ``get_balance``
   capability. Lets the CFO skip typing an opening balance by hand. Falls back
   (returns ``None``) when no provider supports it or the fetch fails — it NEVER
   raises, so a bank-link outage can't 500 the dashboard. The local-first `mock`
   adapter returns a deterministic figure, so `pnpm dev` needs no real bank
   credential.

2. ``resolve_cash_thresholds`` / ``store_cash_thresholds`` — read/normalise the
   per-org alert thresholds persisted on ``Organization.settings.cashflow``
   (JSON — no migration). The cash-position endpoint reads the persisted
   ``min_balance_threshold`` when the request doesn't override it.

Money is `Decimal` end-to-end; nothing here logs an account number (the adapter
returns only an opaque ``account_ref`` label, never a full PAN/account number).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


@dataclass
class ProviderBalance:
    """A funding-account balance resolved from the configured payment provider.

    `account_ref` is the adapter's opaque account label (e.g. "mock-operating"),
    safe to show in the UI — never a full account/routing number."""

    amount: Decimal
    currency: str
    provider: str
    account_ref: str | None = None


async def fetch_provider_balance(payment_config: dict | None) -> ProviderBalance | None:
    """Best-effort current funding-account balance from the org's payment adapter.

    Returns ``None`` (caller falls back to the manual opening balance) when:
      - the adapter doesn't implement the optional ``get_balance`` capability
        (its `BalanceResult.available` is False — the base-class default), or
      - the adapter raised (transport / credential failure).

    Never raises and never logs the balance figure or any account number — only
    the provider name + a coarse reason, so a bank-link outage degrades to the
    BYO opening balance instead of breaking the cash-position view.
    """
    # Import here (not module top) so this service has no import-time dependency
    # on the adapter registry; matches the lazy-import posture elsewhere.
    from app.services.payment_adapters import get_payment_adapter

    try:
        adapter = get_payment_adapter(payment_config)
        result = await adapter.get_balance()
    except Exception:  # noqa: BLE001 — a provider failure must not break the dashboard
        provider = (payment_config or {}).get("provider", "mock")
        logger.warning("cash-position balance fetch failed for provider %s", provider)
        return None

    if not result.available:
        return None
    return ProviderBalance(
        amount=Decimal(str(result.amount)),
        currency=result.currency,
        provider=adapter.provider_name,
        account_ref=result.account_ref,
    )


# ---------------------------------------------------------------------------
# Persisted cash-position alert thresholds (Organization.settings.cashflow)
# ---------------------------------------------------------------------------


@dataclass
class CashThresholds:
    """Per-org persisted cash-position alert thresholds.

    ``min_balance_threshold`` — the low-balance warning level a projected
    period's closing balance must stay at or above; periods below it are flagged
    and collected as breaches. ``None`` means "no persisted threshold" (the
    dashboard simply doesn't flag breaches unless the request passes one)."""

    min_balance_threshold: Decimal | None = None


def _coerce_decimal(raw) -> Decimal | None:
    """Parse a persisted/JSON money value into Decimal, tolerating a malformed
    stored value by returning ``None`` (a corrupt settings blob must never break
    the read)."""
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def resolve_cash_thresholds(settings: dict | None) -> CashThresholds:
    """Read the persisted thresholds off ``Organization.settings.cashflow``.

    Tolerant: a missing/malformed ``cashflow`` block or a non-numeric stored
    value yields an all-``None`` ``CashThresholds`` (no thresholds) rather than
    raising."""
    cashflow = (settings or {}).get("cashflow")
    if not isinstance(cashflow, dict):
        return CashThresholds()
    return CashThresholds(
        min_balance_threshold=_coerce_decimal(cashflow.get("min_balance_threshold")),
    )


def store_cash_thresholds(settings: dict | None, thresholds: CashThresholds) -> dict:
    """Return a NEW settings dict with the thresholds written under ``cashflow``.

    Preserves any other keys already on the ``cashflow`` block (e.g. a manually
    set ``opening_balance``) and stores the threshold as a string so the money
    value round-trips through JSON without going through a float. A ``None``
    threshold clears the persisted key."""
    new_settings = dict(settings or {})
    cashflow = dict(new_settings.get("cashflow") or {})
    if thresholds.min_balance_threshold is None:
        cashflow.pop("min_balance_threshold", None)
    else:
        cashflow["min_balance_threshold"] = str(thresholds.min_balance_threshold)
    new_settings["cashflow"] = cashflow
    return new_settings
