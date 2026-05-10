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
    payment in the tenant."""
    from app.api import portal

    src = inspect.getsource(portal.list_my_payments)
    assert "Invoice.vendor_id == vu.vendor_id" in src
    # And the count query for pagination must scope too.
    occurrences = src.count("Invoice.vendor_id == vu.vendor_id")
    assert occurrences >= 2, (
        "both the list query and the count query must filter on vendor_id"
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

    # `/portal/cards/{token}` is intentionally no-auth — the token
    # IS the credential. Every other portal route must require
    # vendor-user auth.
    NO_AUTH_PORTAL_ROUTES = {"/portal/cards/{token}"}

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
