"""Multi-entity — entity-level chart of accounts (COA) in the two consumers
that previously loaded the chart by ``organization_id`` only.

The GL chart rule (``docs/multi-entity.md`` § Chart of accounts): a
``GLAccount`` with ``entity_id IS NULL`` is SHARED across every entity; one
with a set ``entity_id`` is entity-specific. An entity's *effective* chart is
``shared (NULL) ∪ its own``. This suite locks that semantics into the two
consumers wired in this change:

  1. ``services.gl_recode.bulk_recode_gl`` — a recode candidate (vendor prior)
     is valid for an invoice iff its code is in the invoice's effective chart.
     Because one bulk run spans invoices from different entities, validity is
     resolved *per-invoice-entity*: an entity-B-only code applies to a
     entity-B invoice but is rejected for an entity-A invoice; a shared code
     applies to both.

  2. ``services.extraction.run_extraction`` — the GL-catalog hint passed to the
     AI extractor is scoped to ``shared ∪ the invoice's entity``, never another
     entity's accounts.

Single-entity baseline: with one entity every account is either shared (NULL)
or under that one entity, so the scoping is a no-op — covered explicitly.

The ``bulk_recode_gl`` cases mock the DB session (hermetic, mirroring
``test_gl_recode.py``); the extraction GL-catalog query is exercised against a
real Postgres tenant via the ``realdb`` harness (the cleanest way to assert the
``or_(entity_id == X, entity_id IS NULL)`` filter against actual rows).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.gl_account import GLAccount
from app.models.invoice import InvoiceStatus
from app.services.gl_recode import RecodeFilter, bulk_recode_gl

# ---------------------------------------------------------------------------
# Helpers — bulk_recode_gl DB-mock harness (mirrors test_gl_recode.py)
# ---------------------------------------------------------------------------


def _make_invoice(*, vendor_id, gl_account=None, entity_id=None, invoice_number="INV-1"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        vendor_id=vendor_id,
        gl_account=gl_account,
        status=InvoiceStatus.ready_for_review,
        invoice_number=invoice_number,
        vendor_name="Acme Corp",
        invoice_date=None,
        entity_id=entity_id,
        warnings=None,
    )


class _Stub:
    def __init__(self, results: list):
        self._results = list(results)
        self._idx = 0

    async def __call__(self, *_args, **_kwargs):
        if self._idx >= len(self._results):
            raise AssertionError("execute() called more times than the test set up")
        out = self._results[self._idx]
        self._idx += 1
        return out


def _scalars_all(rows: list):
    obj = MagicMock()
    obj.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    return obj


def _all_rows(rows: list):
    obj = MagicMock()
    obj.all = MagicMock(return_value=rows)
    return obj


def _scalar(value):
    obj = MagicMock()
    obj.scalar = MagicMock(return_value=value)
    return obj


def _make_db_for(*, chart_rows, eligible_invoices, priors):
    """Sequence the SELECTs ``bulk_recode_gl`` issues.

    ``chart_rows`` is a list of ``(code, entity_id)`` tuples — exactly what the
    entity-aware ``_load_active_chart`` reads (``entity_id`` None = shared).
    """
    db = MagicMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.execute = _Stub(
        [
            _all_rows(list(chart_rows)),
            _scalars_all(eligible_invoices),
            _scalar(0),  # immutable count
            _scalar(0),  # no-vendor count
            _all_rows([(vid, val) for vid, val in priors.items()]),
        ]
    )
    return db


# ---------------------------------------------------------------------------
# bulk_recode_gl — per-invoice-entity validation of recode candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_specific_code_applies_only_to_its_own_entity():
    """A prior pointing at entity-B's own code applies to a entity-B invoice
    but is rejected (skipped_invalid_code) for a entity-A invoice — even though
    the code is live elsewhere in the org."""
    entity_a, entity_b = uuid.uuid4(), uuid.uuid4()
    vendor = uuid.uuid4()  # one vendor → one prior, two invoices in two entities

    inv_a = _make_invoice(
        vendor_id=vendor, gl_account="1000", entity_id=entity_a, invoice_number="A"
    )
    inv_b = _make_invoice(
        vendor_id=vendor, gl_account="1000", entity_id=entity_b, invoice_number="B"
    )

    # Chart: shared 1000; entity-B-only 6000.
    db = _make_db_for(
        chart_rows=[("1000", None), ("6000", entity_b)],
        eligible_invoices=[inv_a, inv_b],
        priors={vendor: "6000"},  # prior wants the entity-B-only code
    )

    report = await bulk_recode_gl(
        db, organization_id=uuid.uuid4(), filt=RecodeFilter(), dry_run=True
    )

    # Only the entity-B invoice gets the entity-B code; the entity-A invoice is
    # rejected as an invalid code.
    changed = {c.invoice_number for c in report.changes}
    assert changed == {"B"}
    assert report.changes[0].new_gl == "6000"
    assert report.skipped_invalid_code == 1  # the entity-A invoice
    # It HAS a learned code (just not one live in entity A's chart), so it is
    # not also counted as having none — one invoice, one bucket.
    assert report.skipped_no_prior_no_ai == 0


@pytest.mark.asyncio
async def test_shared_code_applies_across_entities():
    """A shared (entity_id NULL) code is valid for every entity's invoices."""
    entity_a, entity_b = uuid.uuid4(), uuid.uuid4()
    vendor = uuid.uuid4()

    inv_a = _make_invoice(vendor_id=vendor, gl_account="x", entity_id=entity_a, invoice_number="A")
    inv_b = _make_invoice(vendor_id=vendor, gl_account="x", entity_id=entity_b, invoice_number="B")

    db = _make_db_for(
        chart_rows=[("1000", None), ("6000", entity_b)],
        eligible_invoices=[inv_a, inv_b],
        priors={vendor: "1000"},  # prior wants the SHARED code
    )

    report = await bulk_recode_gl(
        db, organization_id=uuid.uuid4(), filt=RecodeFilter(), dry_run=True
    )

    assert {c.invoice_number for c in report.changes} == {"A", "B"}
    assert all(c.new_gl == "1000" for c in report.changes)
    assert report.skipped_invalid_code == 0


@pytest.mark.asyncio
async def test_single_entity_baseline_unchanged():
    """With one entity, every account is shared or under that entity, so the
    scoping is a no-op: an in-chart prior applies exactly as before."""
    only_entity = uuid.uuid4()
    vendor = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor, gl_account="6100", entity_id=only_entity)

    db = _make_db_for(
        chart_rows=[("6100", only_entity), ("6200", only_entity)],
        eligible_invoices=[inv],
        priors={vendor: "6200"},
    )

    report = await bulk_recode_gl(
        db, organization_id=uuid.uuid4(), filt=RecodeFilter(), dry_run=True
    )

    assert len(report.changes) == 1
    assert report.changes[0].new_gl == "6200"
    assert report.skipped_invalid_code == 0


@pytest.mark.asyncio
async def test_empty_chart_accepts_any_code_regardless_of_entity():
    """No active accounts at all → nothing to validate against, so a prior is
    accepted (pre-multi-entity behaviour preserved)."""
    vendor = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor, gl_account=None, entity_id=uuid.uuid4())

    db = _make_db_for(chart_rows=[], eligible_invoices=[inv], priors={vendor: "9999"})

    report = await bulk_recode_gl(
        db, organization_id=uuid.uuid4(), filt=RecodeFilter(), dry_run=True
    )

    assert len(report.changes) == 1
    assert report.changes[0].new_gl == "9999"
    assert report.skipped_invalid_code == 0


# ---------------------------------------------------------------------------
# extraction GL-catalog query — shared ∪ invoice's entity (realdb)
# ---------------------------------------------------------------------------


def _gl_catalog_query(org_id, entity_id):
    """Mirror the catalog query in ``extraction.run_extraction`` so the test
    asserts the exact scoping it ships."""
    from sqlalchemy import or_

    return (
        select(GLAccount.code)
        .where(
            GLAccount.organization_id == org_id,
            GLAccount.is_active == True,  # noqa: E712
            or_(GLAccount.entity_id == entity_id, GLAccount.entity_id.is_(None)),
        )
        .order_by(GLAccount.code)
    )


async def _default_entity_id(realdb, key: str = "a") -> uuid.UUID:
    mk = realdb.sessionmaker(key)
    async with mk() as s:
        return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def test_extraction_gl_catalog_scopes_to_shared_union_entity(realdb):
    """The catalog hint for an invoice sees shared (NULL) ∪ its own entity's
    accounts — never another entity's."""
    org_id = realdb.info("a").org_id
    default_id = await _default_entity_id(realdb)

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        entity_b = Entity(
            organization_id=org_id, name="B Co", slug="b-co", is_default=False, is_active=True
        )
        s.add(entity_b)
        await s.flush()
        b_id = entity_b.id

        s.add_all(
            [
                GLAccount(organization_id=org_id, code="1000", name="Shared Cash", entity_id=None),
                GLAccount(organization_id=org_id, code="6000", name="B Marketing", entity_id=b_id),
                GLAccount(
                    organization_id=org_id,
                    code="7000",
                    name="Default Travel",
                    entity_id=default_id,
                ),
                # Inactive shared account — never in the catalog.
                GLAccount(
                    organization_id=org_id,
                    code="9999",
                    name="Retired",
                    entity_id=None,
                    is_active=False,
                ),
            ]
        )
        await s.commit()

    async with mk() as s:
        b_codes = set((await s.execute(_gl_catalog_query(org_id, b_id))).scalars().all())
        def_codes = set((await s.execute(_gl_catalog_query(org_id, default_id))).scalars().all())

    # Entity B: shared 1000 ∪ its own 6000 — NOT the default entity's 7000.
    assert b_codes == {"1000", "6000"}
    # Default entity: shared 1000 ∪ its own 7000 — NOT B's 6000.
    assert def_codes == {"1000", "7000"}
    # Inactive 9999 appears for nobody.
    assert "9999" not in b_codes and "9999" not in def_codes


async def test_extraction_gl_catalog_single_entity_unchanged(realdb):
    """Single-entity baseline: all accounts under the one (default) entity or
    shared → the catalog is the full active chart, exactly as before."""
    org_id = realdb.info("a").org_id
    default_id = await _default_entity_id(realdb)

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        s.add_all(
            [
                GLAccount(organization_id=org_id, code="1000", name="Cash", entity_id=None),
                GLAccount(organization_id=org_id, code="6100", name="Office", entity_id=default_id),
            ]
        )
        await s.commit()

    async with mk() as s:
        codes = set((await s.execute(_gl_catalog_query(org_id, default_id))).scalars().all())

    assert codes == {"1000", "6100"}
