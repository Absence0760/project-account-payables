"""Composite vendor risk scoring.

NOTE: foundation stub. The real scoring model is implemented by the
"risk scoring & adverse media" worker. Public symbol consumed by
`api/vendor_risk.py` and tests (`compute_vendor_risk`) is fixed — keep it
stable.

Design — a pure, compute-on-demand 0–100 score blended from three signals
(no external calls; mirrors `services/adaptive_workflows.py` style):

  * sanctions — latest `sanctions_checks.result` / `risk_score` for the
    vendor (match → critical, review_required → elevated),
  * fraud signals — count of open `fraud_flag` exceptions on the vendor's
    invoices,
  * payment history — trailing volume / count / any failed or voided runs.

`compute_vendor_risk` returns the breakdown; `recompute_and_persist`
writes `risk_score` / `risk_level` / `risk_factors` / `risk_scored_at`
onto the vendor row (PII-free factors — counts / scores / list NAMES only).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vendor import Vendor


@dataclass
class RiskAssessment:
    """Computed risk for one vendor."""

    risk_score: Decimal | None = None
    risk_level: str = "unknown"  # low | medium | high | critical | unknown
    factors: dict = field(default_factory=dict)


async def compute_vendor_risk(
    db: AsyncSession,
    *,
    vendor: Vendor,
    organization_id: uuid.UUID,
    org_settings: dict | None = None,
) -> RiskAssessment:
    """Compute (do not persist) a vendor's composite risk.

    Foundation stub — returns `unknown`. Implemented by the risk-scoring
    worker.
    """
    return RiskAssessment()
