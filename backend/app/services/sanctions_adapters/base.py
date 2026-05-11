"""Sanctions adapter contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class ScreeningResult:
    """Outcome of a single screening call.

    `result` is the load-bearing field for the orchestrator:
      - "clear"            → proceed
      - "match"            → refuse the payment, raise exception
      - "review_required"  → open a review-queue exception, hold
                             the payment in pending_compliance

    `matched_list` names the list the hit came from (OFAC SDN, EU
    consolidated, UK HMT, UN, PEP, ...) so the AP team's review can
    cite the source. `risk_score` is 0–100 from the provider when
    available; the orchestrator's threshold is per-org.

    The raw response is preserved so an auditor can replay the call.
    PII concern: sanctions providers return free-form match details
    that may include date-of-birth, passport numbers, addresses. We
    store these only in the JSONB column and NEVER echo them in
    logs or HTTP responses — see invariant #7.
    """

    provider: str
    result: str  # 'clear' | 'match' | 'review_required'
    matched_list: str | None = None
    risk_score: Decimal | None = None
    raw_response: dict = field(default_factory=dict)


class SanctionsAdapter(Protocol):
    """Minimum contract every sanctions / PEP provider must satisfy."""

    provider_name: str

    async def screen_vendor(
        self,
        *,
        vendor_name: str,
        vendor_country: str | None,
        vendor_tax_id: str | None = None,
        beneficial_owners: list[dict] | None = None,
    ) -> ScreeningResult:
        """Submit one vendor's identity payload to the provider.

        `beneficial_owners` is a list of `{name, country, dob, ...}`
        dicts when the entity is a corporation; providers screen
        each owner against the same lists. Pass an empty list for
        sole proprietors / individuals.
        """
        ...

    async def test_connection(self) -> bool:
        """Cheapest available probe (auth check or empty-query
        response). True on success."""
        ...
