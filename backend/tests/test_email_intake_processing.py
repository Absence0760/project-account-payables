"""Tests for the email-intake processing core.

``test_email_intake.py`` covers tokens, address parsing, signature verify,
and the provider adapters. This file covers the two untested pieces with
real security weight:

  * ``resolve_tenant_from_recipient`` — the tenant-resolution chokepoint:
    only an *enabled* token whose value matches resolves; a disabled or
    unknown token resolves to None.
  * ``process_inbound_email`` orchestration — unknown recipient short-circuits
    with no tenant engine / S3 write; the happy path creates one invoice per
    usable attachment and dispatches extraction once per invoice with the
    ``SYSTEM_ACTOR_ID`` (email intake has no human actor).
  * ``_create_invoice_from_attachment`` — the seeded invoice is ``pending``,
    ``amount == Decimal('0')`` (exact, not float), no human uploader, and the
    S3 object is written under the org-prefixed key.

Note on the audit trail: an intake invoice is *created* (not transitioned),
and — consistent with the regular ``POST /api/invoices`` create path, which
also writes no audit row on create — intake writes none either. The module
docstring's "every audit trail ... points at SYSTEM_ACTOR_ID" refers to the
actor carried into the downstream extraction trail (asserted below via the
dispatch_extraction actor), not an audit row on create. So there is no
inconsistency to fix here.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.invoice import InvoiceStatus
from app.services import email_intake
from app.services.email_intake import (
    InboundAttachment,
    InboundEmail,
    _create_invoice_from_attachment,
)

# ---------------------------------------------------------------------------
# resolve_tenant_from_recipient
# ---------------------------------------------------------------------------


def _org(*, token: str, enabled: bool, slug: str = "acme") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug=slug,
        db_name=f"ap_{slug}",
        settings={"email_intake": {"token": token, "enabled": enabled}},
    )


class _FakeOrgsCtrl:
    """Control-plane session whose execute() yields a fixed org list."""

    def __init__(self, orgs: list) -> None:
        self._orgs = orgs

    async def execute(self, *_a, **_k):
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=self._orgs)
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars)
        return result


async def test_resolve_tenant_matches_enabled_token():
    org_a = _org(token="aaa", enabled=True, slug="acme")
    org_b = _org(token="bbb", enabled=True, slug="beta")
    ctrl = _FakeOrgsCtrl([org_a, org_b])
    got = await email_intake.resolve_tenant_from_recipient(ctrl, "invoices+bbb@ap.co")
    assert got is org_b


async def test_resolve_tenant_disabled_token_returns_none():
    org = _org(token="aaa", enabled=False)
    ctrl = _FakeOrgsCtrl([org])
    assert await email_intake.resolve_tenant_from_recipient(ctrl, "invoices+aaa@ap.co") is None


async def test_resolve_tenant_unknown_token_returns_none():
    org = _org(token="aaa", enabled=True)
    ctrl = _FakeOrgsCtrl([org])
    assert await email_intake.resolve_tenant_from_recipient(ctrl, "invoices+zzz@ap.co") is None


# ---------------------------------------------------------------------------
# process_inbound_email — orchestration
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self) -> None:
        self.commit = AsyncMock()
        # Multi-entity Phase 2: the process loop resolves the tenant's default
        # entity id (one SELECT) before creating invoices. Hand back a value so
        # that lookup succeeds.
        _result = MagicMock()
        _result.scalar_one_or_none = MagicMock(return_value=uuid.uuid4())
        self.execute = AsyncMock(return_value=_result)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False


def _pdf(name="bill.pdf") -> InboundAttachment:
    return InboundAttachment(filename=name, content_type="application/pdf", content=b"%PDF-1.4")


async def test_process_unknown_recipient_returns_error_and_touches_nothing():
    create_engine = MagicMock()
    get_client = MagicMock()
    with (
        patch.object(email_intake, "resolve_tenant_from_recipient", AsyncMock(return_value=None)),
        patch("sqlalchemy.ext.asyncio.create_async_engine", create_engine),
        patch("app.services.storage._get_client", get_client),
    ):
        result = await email_intake.process_inbound_email(
            MagicMock(), InboundEmail(to="invoices+nope@ap.co", sender="v@x.com")
        )
    assert result.error == "Unknown or disabled intake address"
    assert result.invoices_created == []
    create_engine.assert_not_called()
    get_client.assert_not_called()


async def test_process_no_usable_attachments_returns_error():
    org = _org(token="aaa", enabled=True)
    create_engine = MagicMock()
    bad = InboundAttachment(filename="memo.docx", content_type="application/msword", content=b"x")
    with (
        patch.object(email_intake, "resolve_tenant_from_recipient", AsyncMock(return_value=org)),
        patch("sqlalchemy.ext.asyncio.create_async_engine", create_engine),
    ):
        result = await email_intake.process_inbound_email(
            MagicMock(),
            InboundEmail(to="invoices+aaa@ap.co", sender="v@x.com", attachments=[bad]),
        )
    assert result.error == "No usable PDF / image attachments"
    assert "memo.docx" in result.skipped_attachments[0]
    create_engine.assert_not_called()


async def test_process_creates_invoice_per_attachment_and_dispatches_system_extraction():
    org = _org(token="aaa", enabled=True, slug="acme")
    ids = [uuid.uuid4(), uuid.uuid4()]
    session = _FakeSession()
    engine = MagicMock(dispose=AsyncMock())
    dispatch = AsyncMock()
    with (
        patch.object(email_intake, "resolve_tenant_from_recipient", AsyncMock(return_value=org)),
        patch.object(email_intake, "_create_invoice_from_attachment", AsyncMock(side_effect=ids)),
        patch(
            "app.database._make_tenant_url",
            MagicMock(return_value="postgresql+asyncpg://x/ap_acme"),
        ),
        patch("sqlalchemy.ext.asyncio.create_async_engine", MagicMock(return_value=engine)),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", MagicMock(return_value=lambda: session)),
        patch("app.services.storage._get_client", MagicMock(return_value=MagicMock())),
        patch("app.services.storage._ensure_bucket", MagicMock()),
        patch("app.services.extraction_dispatch.dispatch_extraction", dispatch),
    ):
        result = await email_intake.process_inbound_email(
            MagicMock(),
            InboundEmail(
                to="invoices+aaa@ap.co",
                sender="v@x.com",
                attachments=[_pdf("a.pdf"), _pdf("b.pdf")],
            ),
        )

    assert result.tenant_slug == "acme"
    assert result.invoices_created == ids
    session.commit.assert_awaited_once()
    engine.dispose.assert_awaited_once()
    # One extraction dispatch per invoice, each attributed to the system actor.
    assert dispatch.await_count == 2
    for call, inv_id in zip(dispatch.await_args_list, ids, strict=True):
        assert call.args == (inv_id, org.id, email_intake.SYSTEM_ACTOR_ID)


# ---------------------------------------------------------------------------
# _create_invoice_from_attachment
# ---------------------------------------------------------------------------


class _FakeTenantDB:
    """Tenant session that assigns a real id on flush (mirrors the PK default)."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()


async def test_create_invoice_is_pending_zero_decimal_and_org_prefixed_key():
    db = _FakeTenantDB()
    s3 = MagicMock()
    org_id = uuid.uuid4()
    att = _pdf("bill.pdf")

    entity_id = uuid.uuid4()
    invoice_id = await _create_invoice_from_attachment(
        tenant_db=db,
        org_id=org_id,
        entity_id=entity_id,
        sender="vendor@x.com",
        subject="March invoice",
        attachment=att,
        s3=s3,
    )

    invoice = db.added[0]
    assert invoice.status == InvoiceStatus.pending
    assert invoice.amount == Decimal("0")
    assert isinstance(invoice.amount, Decimal)
    assert invoice.currency == "USD"
    assert invoice.uploaded_by_id is None
    assert invoice.organization_id == org_id
    assert invoice.entity_id == entity_id  # lands under the resolved entity

    expected_key = f"{org_id}/{invoice_id}/bill.pdf"
    assert invoice.file_key == expected_key
    put = s3.put_object.call_args.kwargs
    assert put["Key"] == expected_key
    assert put["ContentType"] == "application/pdf"
    assert put["Body"] == b"%PDF-1.4"
