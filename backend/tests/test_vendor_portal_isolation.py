"""Vendor portal isolation tests — per-vendor query scoping.

Every supplier-portal endpoint must filter by the authenticated
`VendorUser.vendor_id`. Without that scoping, vendor A logged into a
shared tenant DB could read vendor B's invoices, payments, or card
reveal tokens.

We exercise the dep-level guards directly (DB-free) and pin the
contract on every endpoint that reads vendor-scoped data:

  - `list_my_invoices`: SELECT WHERE vendor_id = vu.vendor_id
  - `get_my_invoice`: SELECT WHERE id = ? AND vendor_id = ?
  - `submit_invoice`: rejects POSTs that target another vendor
  - `list_my_payments`: payments join filtered by Invoice.vendor_id
  - `reveal_card`: token consumption is scoped to the card's vendor
    binding (the token is its own credential, but the card's vendor
    id must match the binding the issuer recorded)
"""

from __future__ import annotations

import inspect

import pytest

# ---------------------------------------------------------------------------
# Static-analysis contract: every read-many endpoint must filter on vendor_id
# ---------------------------------------------------------------------------


def test_list_my_invoices_filters_by_vendor_id():
    """Source contract: the query must include a vendor_id filter
    bound to the authenticated VendorUser. A regression that drops
    the filter (e.g., adds pagination above the WHERE clause) leaks
    every invoice in the tenant to every portal user."""
    from app.api import portal

    src = inspect.getsource(portal.list_my_invoices)
    assert "vu.vendor_id" in src, (
        "list_my_invoices must scope its query on the authenticated vendor's id"
    )
    assert "Invoice.vendor_id" in src


def test_get_my_invoice_filters_by_vendor_id():
    """A direct-id lookup is the highest-risk shape — if the WHERE
    clause is only `id = ?`, any vendor who knows another invoice's
    UUID can read it. Pin that the WHERE clause AND-s on vendor_id."""
    from app.api import portal

    src = inspect.getsource(portal.get_my_invoice)
    # Must reference both the id and the vendor_id in the same query.
    assert "Invoice.id" in src
    assert "Invoice.vendor_id" in src
    assert "vu.vendor_id" in src


def test_submit_invoice_uses_vendor_id_for_ownership():
    """When the portal submits a new invoice, the vendor_id stamped
    on the row must be the authenticated `vu.vendor_id`, never a
    value from the request body. Otherwise a vendor user could
    submit on another vendor's behalf."""
    from app.api import portal

    src = inspect.getsource(portal.submit_invoice)
    # The vendor lookup is bound to vu.vendor_id, not body.vendor_id.
    assert "Vendor.id == vu.vendor_id" in src


def test_list_my_payments_filters_by_invoice_vendor_id():
    """Payments are joined through Invoice, so the scoping clause
    appears on the Invoice side. Without it, a portal user sees every
    payment in the tenant.

    The handler builds ONE `filters` list (`Invoice.vendor_id ==` first,
    optional status/search appended) and spreads it into BOTH the list query
    and the count query — so the guarantee is "the same filters feed both
    `.where(*filters)` calls", not "the literal string appears twice".
    Behavioural proof that a filter can't widen past the vendor is
    `test_portal_payment_filters.py::test_payment_filters_stay_within_the_callers_vendor`.
    """
    from app.api import portal

    src = inspect.getsource(portal.list_my_payments)
    assert "Invoice.vendor_id == vu.vendor_id" in src
    assert src.count(".where(*filters)") >= 2, (
        "both the list query and the count query must apply the shared vendor-scoped filters"
    )


# ---------------------------------------------------------------------------
# Behavioral: get_my_invoice with a non-matching vendor_id returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_my_invoice_404s_when_invoice_belongs_to_a_different_vendor():
    """If the invoice exists but vendor_id != vu.vendor_id, the
    SELECT returns no row → 404. The handler must NOT fall back to
    a second unscoped query."""
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import HTTPException

    from app.api.portal import get_my_invoice

    # Mock the DB: scoped query returns None (no row matches both id+vendor).
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    vu = SimpleNamespace(id=uuid.uuid4(), vendor_id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await get_my_invoice(invoice_id=uuid.uuid4(), db=db, vu=vu)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_my_invoice_succeeds_when_vendor_matches():
    """Positive control — when the invoice IS this vendor's, the
    endpoint returns the row (proves the WHERE clause isn't always
    failing)."""
    import uuid
    from datetime import UTC, datetime
    from decimal import Decimal
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.api.portal import get_my_invoice
    from app.models.invoice import InvoiceStatus

    invoice = SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        invoice_number="INV-1",
        amount=Decimal("100.00"),
        currency="USD",
        invoice_date=datetime.now(UTC).date(),
        received_date=datetime.now(UTC).date(),
        due_date=None,
        status=InvoiceStatus.new,
        description=None,
        created_at=datetime.now(UTC),
        file_key=None,
        file_url=None,
    )

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=invoice)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    vu = SimpleNamespace(id=uuid.uuid4(), vendor_id=invoice.vendor_id)

    resp = await get_my_invoice(invoice_id=invoice.id, db=db, vu=vu)
    assert resp.id == str(invoice.id)


# ---------------------------------------------------------------------------
# Vendor user dep — wrong-typ token cannot reach portal endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portal_endpoint_dep_rejects_employee_token():
    """`get_current_vendor_user` lives in `portal_deps`. A previous
    test pinned the rejection at the dep level; here we re-confirm
    that the portal router endpoints (e.g., list_my_invoices) declare
    that dep — without it, the typed-token guard isn't reached."""
    from fastapi.routing import APIRoute

    from app.api import portal

    # Intentionally no-auth portal routes (mirrors test_rbac.NO_AUTH_REQUIRED):
    #   - `/portal/cards/{token}` — the single-use card-reveal token IS the
    #     credential (no JWT, no vendor auth).
    #   - `/portal/branding` — public-by-design white-label brand read for the
    #     unauthenticated supplier-portal login page; returns only non-sensitive
    #     BrandConfig fields, tenant resolved by `get_tenant`. See docs/white-label.md.
    # Every OTHER portal route must require vendor-user auth.
    NO_AUTH_PORTAL_ROUTES = {"/portal/cards/{token}", "/portal/branding"}

    portal_routes = [r for r in portal.router.routes if isinstance(r, APIRoute)]
    for route in portal_routes:
        if route.path in NO_AUTH_PORTAL_ROUTES:
            continue
        src = inspect.getsource(route.endpoint)
        assert "get_current_vendor_user" in src, (
            f"Portal route {route.path} does not depend on get_current_vendor_user — "
            f"a regression here would expose the endpoint to unauthenticated callers."
        )


# ---------------------------------------------------------------------------
# get_current_vendor_user — the portal user's org must match the resolved tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vendor_user_dep_rejects_org_mismatch():
    """A VendorUser row whose `organization_id` does not match the resolved
    tenant must be refused (opaque 401), even though the id lookup succeeded —
    this is the positive tenant binding that no longer rests on a UUID-collision
    assumption. If a colliding VendorUser.id existed in the wrong tenant DB, its
    org wouldn't match the requested tenant, so it can't authenticate."""
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import HTTPException

    from app.api.deps import create_vendor_access_token
    from app.api.portal_deps import get_current_vendor_user

    vu_id = uuid.uuid4()
    row_org = uuid.uuid4()
    requested_tenant_org = uuid.uuid4()  # a DIFFERENT org than the row's

    vu = SimpleNamespace(id=vu_id, vendor_id=uuid.uuid4(), is_active=True, organization_id=row_org)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=vu)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    tenant = SimpleNamespace(id=requested_tenant_org)
    token = create_vendor_access_token(vu_id, vu.vendor_id)

    with patch("app.api.portal_deps.is_token_blocked", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc:
            await get_current_vendor_user(authorization=f"Bearer {token}", tenant=tenant, db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_vendor_user_dep_accepts_matching_org():
    """Positive control — a VendorUser whose org matches the resolved tenant
    authenticates (the guard isn't rejecting everything)."""
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.api.deps import create_vendor_access_token
    from app.api.portal_deps import get_current_vendor_user

    vu_id = uuid.uuid4()
    org_id = uuid.uuid4()
    vu = SimpleNamespace(id=vu_id, vendor_id=uuid.uuid4(), is_active=True, organization_id=org_id)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=vu)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    tenant = SimpleNamespace(id=org_id)
    token = create_vendor_access_token(vu_id, vu.vendor_id)

    with patch("app.api.portal_deps.is_token_blocked", AsyncMock(return_value=False)):
        got = await get_current_vendor_user(authorization=f"Bearer {token}", tenant=tenant, db=db)
    assert got is vu


@pytest.mark.asyncio
async def test_vendor_user_dep_tolerates_null_org_for_legacy_rows():
    """A legacy (un-backfilled) row with organization_id=NULL still
    authenticates — the additive migration is nullable and the DB-per-tenant
    boundary still holds; the positive check only fires when the org is set."""
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.api.deps import create_vendor_access_token
    from app.api.portal_deps import get_current_vendor_user

    vu_id = uuid.uuid4()
    vu = SimpleNamespace(id=vu_id, vendor_id=uuid.uuid4(), is_active=True, organization_id=None)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=vu)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    tenant = SimpleNamespace(id=uuid.uuid4())
    token = create_vendor_access_token(vu_id, vu.vendor_id)

    with patch("app.api.portal_deps.is_token_blocked", AsyncMock(return_value=False)):
        got = await get_current_vendor_user(authorization=f"Bearer {token}", tenant=tenant, db=db)
    assert got is vu


# ---------------------------------------------------------------------------
# Card reveal token — single-use + lookup is by token, not vendor
# ---------------------------------------------------------------------------


def test_card_reveal_endpoint_does_not_take_vendor_id_param():
    """The reveal endpoint is the rare case where the token IS the
    credential — there's no JWT, no vendor auth. The token's
    single-use + hashed-at-rest guarantees handle authorization. We
    pin that the endpoint signature doesn't accept a vendor_id /
    invoice_id / tenant override that could let an attacker pivot a
    valid token onto another card."""
    from app.api import portal

    sig = inspect.signature(portal.reveal_card)
    forbidden = {"vendor_id", "invoice_id", "tenant_slug", "card_id"}
    leaks = set(sig.parameters) & forbidden
    assert not leaks, (
        f"reveal_card grew unsafe parameter(s) {leaks} — the token alone authorises;"
        f" any other identity input is a pivot vector."
    )


# ---------------------------------------------------------------------------
# Chat-attachment download — the key must belong to the OWNED invoice,
# not merely share the tenant's org prefix (cross-vendor IDOR guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_file_download_rejects_other_invoice_key_in_same_org():
    """A vendor passes their OWN invoice id in the path (ownership check
    passes) but a `file_key` pointing at ANOTHER invoice's chat attachment
    in the same tenant. Chat keys are `<org>/chat/<invoice>/<msg>/<file>`
    and every vendor in a tenant shares the same `<org>` segment, so an
    org-prefix-only check would serve the victim's file. The handler must
    bind the key to the ownership-checked invoice and 404 otherwise."""
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.api import portal

    org_id = uuid.uuid4()
    my_invoice_id = uuid.uuid4()
    victim_invoice_id = uuid.uuid4()
    my_vendor_id = uuid.uuid4()
    my_inv = SimpleNamespace(id=my_invoice_id, organization_id=org_id, vendor_id=my_vendor_id)
    vu = SimpleNamespace(vendor_id=my_vendor_id)

    # Key for a DIFFERENT invoice in the SAME org.
    victim_key = f"{org_id}/chat/{victim_invoice_id}/{uuid.uuid4()}/secret.pdf"

    async def _own_invoice(db, invoice_id, _vu):
        assert invoice_id == my_invoice_id
        return my_inv

    served = {"called": False}

    async def _fake_get_file(_key):
        served["called"] = True
        return b"VICTIM-BYTES", "application/pdf"

    from fastapi import HTTPException

    with (
        patch.object(portal, "_portal_invoice_or_404", _own_invoice),
        patch.object(portal, "get_file", _fake_get_file),
    ):
        with pytest.raises(HTTPException) as exc:
            await portal.get_portal_chat_file(
                invoice_id=my_invoice_id, file_key=victim_key, db=AsyncMock(), vu=vu
            )
    assert exc.value.status_code == 404
    # The fix must short-circuit BEFORE touching storage — otherwise the bytes
    # left S3 even if the response is a 404.
    assert served["called"] is False, "victim attachment was fetched from storage"


@pytest.mark.asyncio
async def test_chat_file_download_serves_own_invoice_key():
    """Positive control — a key under the vendor's OWN invoice prefix is
    served, so the guard above isn't passing for the wrong reason."""
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.api import portal

    org_id = uuid.uuid4()
    my_invoice_id = uuid.uuid4()
    my_vendor_id = uuid.uuid4()
    my_inv = SimpleNamespace(id=my_invoice_id, organization_id=org_id, vendor_id=my_vendor_id)
    vu = SimpleNamespace(vendor_id=my_vendor_id)
    own_key = f"{org_id}/chat/{my_invoice_id}/{uuid.uuid4()}/mine.pdf"

    async def _own_invoice(db, invoice_id, _vu):
        return my_inv

    with (
        patch.object(portal, "_portal_invoice_or_404", _own_invoice),
        patch.object(portal, "get_file", AsyncMock(return_value=(b"MINE", "application/pdf"))),
    ):
        resp = await portal.get_portal_chat_file(
            invoice_id=my_invoice_id, file_key=own_key, db=AsyncMock(), vu=vu
        )
    assert resp.body == b"MINE"
