"""Entity model — a legal entity (subsidiary) within a single tenant.

Multi-entity lets one organization run several subsidiaries inside its one
tenant DB. ``entities`` is tenant-scoped (lives in the tenant DB next to
invoices), and business tables carry a nullable ``entity_id`` FK to it via
``EntityMixin``. Every tenant has exactly one ``is_default`` entity, created at
provisioning (fresh tenants) or by migration 0029 (existing tenants); the
backfill points all pre-existing rows at it, so a single-entity tenant behaves
exactly as before.

This is NOT the multi-tenant boundary — that's still the per-org tenant DB
(see ``app/tenant.py``). Entities subdivide *within* a tenant. See
``docs/multi-entity.md``.
"""

import uuid

from sqlalchemy import Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin


class Entity(Base, TenantMixin, TimestampMixin):
    __tablename__ = "entities"

    __table_args__ = (
        # Slugs are unique within the tenant (one DB == one org).
        Index("uq_entities_slug", "slug", unique=True),
        # At most one default entity per tenant — the partial unique index
        # makes a second default impossible (mirrors the role-uniqueness fix).
        Index(
            "uq_entities_one_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    # ISO 4217. NULL means "use the org's reporting currency"
    # (resolve_reporting_currency) — entities only set this when they report in
    # a different currency than the org default.
    currency: Mapped[str | None] = mapped_column(String(3))
    is_default: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        default=True, server_default=text("true"), nullable=False
    )
    # server_default mirrors migration 0029 so raw INSERTs that omit settings
    # (provisioning, seed, the test harness) work against create_all-built
    # tables, not just migrated ones.
    settings: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
