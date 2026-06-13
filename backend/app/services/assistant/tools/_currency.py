"""Resolve an org's reporting (base) currency from the control plane.

Tools run against the tenant DB but the reporting currency lives in
``Organization.settings`` (control plane). This does a tiny control-plane
lookup, scoped to the caller's own ``org_id``, and degrades to the platform
default rather than raising.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import control_session_factory
from app.models.organization import Organization
from app.services.currency_conversion import resolve_reporting_currency


async def resolve_org_currency(org_id: uuid.UUID, control_db: AsyncSession | None = None) -> str:
    """Look up the org's reporting currency on the control plane.

    Reuses the request-scoped ``control_db`` session when one is supplied (the
    assistant always passes the orchestrator's injected session). Falls back to
    a fresh global-engine session only for callers without one. Reusing the
    injected session matters under the async test harness — opening a second
    session on the module-global engine binds to whatever event loop the global
    pool last touched, which is not necessarily the request's loop.
    """
    if control_db is not None:
        settings_dict = (
            await control_db.execute(select(Organization.settings).where(Organization.id == org_id))
        ).scalar_one_or_none()
        return resolve_reporting_currency(settings_dict)

    async with control_session_factory() as ctrl_db:
        settings_dict = (
            await ctrl_db.execute(select(Organization.settings).where(Organization.id == org_id))
        ).scalar_one_or_none()
    return resolve_reporting_currency(settings_dict)
