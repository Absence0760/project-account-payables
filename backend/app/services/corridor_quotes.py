"""Multi-route corridor optimization.

Given a payment payload + a list of configured payment processors,
ask each for a `CorridorQuote` and pick the best one according to the
org's optimization preference:

  - `cheapest` (default) — minimize total_cost(amount); ties broken
    by faster ETA, then by stable provider name
  - `fastest` — minimize eta_business_days; ties broken by cheaper
    cost, then by stable provider name

A "configured processor" is whatever the org has enabled in
`Organization.settings.payments.providers` (a new optional list of
provider configs). When that field is absent we fall back to the
single `payments.provider` and the optimizer is a no-op (one quote,
one result).

Failed quotes (network error, adapter raise) are treated as
`available=False` so a flaky provider can never *win* the auction.
The aggregator logs the failure class but does NOT include the raw
exception message in the response (invariant #7 — adapters' error
strings can leak provider-side details).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.services.payment_adapters import (
    CorridorQuote,
    PaymentAdapter,
    PaymentPayload,
    UnknownPaymentProviderError,
    get_payment_adapter,
)

logger = logging.getLogger(__name__)


OptimizeMode = Literal["cheapest", "fastest"]


@dataclass(frozen=True)
class QuoteRanking:
    """Outcome of comparing N quotes.

    `winner` is the quote we'd submit; `runners_up` is the rest, in
    rank order, so the UI can show "you saved $X vs Y". `mode`
    records which optimization knob was applied so a later review
    knows whether the choice was cheapest or fastest.
    """

    winner: CorridorQuote
    runners_up: list[CorridorQuote]
    mode: OptimizeMode


class NoEligibleCorridorError(RuntimeError):
    """Raised when zero providers can quote the requested corridor.

    The caller turns this into a payment failure with
    `failure_reason="no_eligible_corridor"`. Distinct from a normal
    adapter failure: it means we asked everyone we have and nobody
    can submit the payment as configured."""


def _provider_configs_from_org_settings(org_settings: dict | None) -> list[dict]:
    """Extract the list of enabled payment processor configs.

    Multi-route is opt-in: a single-provider org's settings have
    `payments.provider` (and optional `payments.credentials`); a
    multi-route org's settings carry `payments.providers` as a list
    of per-provider dicts. We accept both shapes so an org can
    migrate from one to the other without a settings flip.
    """
    payments_cfg = (org_settings or {}).get("payments") or {}
    raw = payments_cfg.get("providers")
    if isinstance(raw, list) and raw:
        return list(raw)
    # Fall back to the legacy single-provider shape.
    single = payments_cfg.get("provider")
    if single:
        # Build a one-element list by lifting the legacy keys into
        # the per-provider shape.
        return [
            {
                "provider": single,
                **{k: v for k, v in payments_cfg.items() if k not in {"provider", "providers"}},
            }
        ]
    return []


async def _quote_one(adapter: PaymentAdapter, payload: PaymentPayload) -> CorridorQuote:
    """Wrap `adapter.quote_payment` so an exception becomes an
    `available=False` quote with a sanitised reason string.

    Crucial: we MUST NOT propagate the adapter's exception message
    into the response — adapters can surface partial PANs / account
    numbers in their error strings (invariant #7). The class name is
    enough for a debugger to follow up; the message is not."""
    try:
        return await adapter.quote_payment(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[corridor_quotes] %s raised on quote for invoice=%s method=%s: %s",
            adapter.provider_name,
            payload.invoice_id,
            payload.method,
            exc.__class__.__name__,
        )
        return CorridorQuote(
            provider=adapter.provider_name,
            method=payload.method,
            available=False,
            unavailable_reason=f"adapter_error:{exc.__class__.__name__}",
        )


def _rank(quotes: list[CorridorQuote], amount: Decimal, mode: OptimizeMode) -> list[CorridorQuote]:
    """Stable sort: best first."""
    if mode == "fastest":
        # eta asc, then cost asc, then provider name asc.
        return sorted(
            quotes,
            key=lambda q: (
                q.eta_business_days if q.available else 10**9,
                q.total_cost(amount),
                q.provider,
            ),
        )
    # cheapest (default): cost asc, then eta asc, then provider name asc.
    return sorted(
        quotes,
        key=lambda q: (
            q.total_cost(amount),
            q.eta_business_days if q.available else 10**9,
            q.provider,
        ),
    )


async def compare_quotes(
    payload: PaymentPayload,
    org_settings: dict | None,
    *,
    mode: OptimizeMode = "cheapest",
) -> QuoteRanking:
    """Gather quotes from every configured provider and rank them.

    Raises `NoEligibleCorridorError` when nobody can quote the
    requested corridor. The caller is then responsible for failing
    the payment with a specific reason — the executor turns this
    into `failure_reason="no_eligible_corridor"`.
    """
    configs = _provider_configs_from_org_settings(org_settings)
    if not configs:
        raise NoEligibleCorridorError("no payment providers configured in org.settings.payments")

    adapters: list[PaymentAdapter] = []
    unsupported: list[CorridorQuote] = []
    for cfg in configs:
        try:
            adapters.append(get_payment_adapter(cfg))
        except UnknownPaymentProviderError as exc:
            # One bad name in a multi-provider list must not take the whole
            # auction down — the org's other rails can still quote. It becomes
            # an unavailable quote so it can never WIN, and the reason is what
            # `NoEligibleCorridorError` reports if it was the only entry.
            logger.warning("[corridor_quotes] skipping unsupported provider in payments.providers")
            unsupported.append(
                CorridorQuote(
                    provider=exc.provider,
                    method=payload.method,
                    available=False,
                    unavailable_reason="provider_not_supported",
                )
            )

    # De-duplicate by provider_name so a doubly-configured org doesn't
    # quote itself twice (and rank one against the other).
    seen: set[str] = set()
    unique_adapters: list[PaymentAdapter] = []
    for a in adapters:
        if a.provider_name in seen:
            continue
        seen.add(a.provider_name)
        unique_adapters.append(a)

    quotes: list[CorridorQuote] = list(unsupported)
    for adapter in unique_adapters:
        quotes.append(await _quote_one(adapter, payload))

    ranked = _rank(quotes, payload.amount, mode)
    if not ranked or not ranked[0].available:
        # Every provider said "no" — surface the most informative
        # reason in the exception message.
        reasons = [q.unavailable_reason or "unavailable" for q in ranked if not q.available]
        msg = (
            f"no provider can quote method={payload.method} "
            f"for {payload.currency}/{payload.target_country or '?'}: "
            f"{'; '.join(reasons) if reasons else 'unknown'}"
        )
        raise NoEligibleCorridorError(msg)

    return QuoteRanking(winner=ranked[0], runners_up=ranked[1:], mode=mode)


def savings_vs_runner_up(ranking: QuoteRanking, amount: Decimal) -> Decimal:
    """How much the winner saves over the next-best provider, in the
    payment's source currency.

    Used by the UI to render "you saved $12.45 vs <provider>" on the
    payment-detail panel. Returns Decimal("0") when there's no
    runner-up (single provider configured)."""
    if not ranking.runners_up:
        return Decimal("0")
    next_best = ranking.runners_up[0]
    if not next_best.available:
        return Decimal("0")
    return next_best.total_cost(amount) - ranking.winner.total_cost(amount)
