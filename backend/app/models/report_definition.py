"""Custom (ad-hoc) report builder — saved report definitions.

A :class:`ReportDefinition` is a tenant-scoped, entity-scoped saved *spec* for
the ad-hoc report builder: which data source, which group-by dimensions, which
aggregate measures, which whitelisted filters, sort, and an optional row limit.
The spec fields are stored as JSONB (small, edited in place via PATCH, never
joined) and mirror the ``ReportSpec`` Pydantic shape in ``app/schemas/report.py``.

The spec never carries a raw column / table name that reaches SQL — only
*catalog keys*. The security boundary that maps keys → real SQLAlchemy columns
lives in ``app/services/report_builder.py``; this model just persists the keys a
user chose. See ``backend/docs/report-builder.md``.
"""

import uuid

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntityMixin, TimestampMixin


class ReportDefinition(Base, EntityMixin, TimestampMixin):
    __tablename__ = "report_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # A catalog KEY (invoices / payments / vendors / expenses) — resolved to a
    # real model + column set only inside the report_builder catalog. Never a
    # raw table name.
    data_source: Mapped[str] = mapped_column(String(50), nullable=False)

    # Spec fragments — lists of dicts, each element a catalog-key reference:
    #   dimensions: [{"key": "vendor_name", "grain": null}]
    #   measures:   [{"key": "amount", "agg": "sum"}]
    #   filters:    [{"key": "status", "op": "in", "value": [...]}]
    #   sort:       [{"key": "amount_sum", "dir": "desc"}]
    # Stored as JSONB so an editor can PATCH one fragment without a join table.
    dimensions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    measures: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    filters: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    sort: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Optional cap on returned rows. Named ``row_limit`` (not ``limit``) to avoid
    # the SQL reserved word; the JSON spec key is ``limit``.
    row_limit: Mapped[int | None] = mapped_column(Integer)

    # Control-plane User id of the author (no cross-DB FK — plain uuid).
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # created_at / updated_at from TimestampMixin.
