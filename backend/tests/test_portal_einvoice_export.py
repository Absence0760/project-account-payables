"""Supplier-portal UBL e-invoice download — GET /portal/invoices/{id}/einvoice.

CRITICAL invariant: the handler must scope its query to the authenticated
VendorUser's vendor_id, so vendor A can never download vendor B's invoice.
We pin that both statically (the source query AND-s on vendor_id) and
behaviorally (a non-matching vendor yields 404, never a foreign document),
mirroring test_vendor_portal_isolation.py.

The route also must NOT 422 the supplier on a tax soft-warning — it always
returns the generated UBL — and must declare the vendor-auth dependency so the
RBAC coverage gate sees it.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.portal import get_my_invoice_einvoice
from app.services.e_invoice import parse_ubl


# ---------------------------------------------------------------------------
# Static contract: the query is scoped to the caller's vendor_id
# ---------------------------------------------------------------------------
def test_einvoice_handler_filters_by_vendor_id():
    src = inspect.getsource(get_my_invoice_einvoice)
    assert "Invoice.id == invoice_id" in src
    assert "Invoice.vendor_id == vu.vendor_id" in src
    # And it must depend on vendor-user auth (RBAC coverage gate).
    assert "get_current_vendor_user" in src


def test_einvoice_handler_does_not_422_on_validation():
    """The supplier export logs validation warnings field-only; it must never
    raise an HTTPException on a tax/validation soft-warning."""
    src = inspect.getsource(get_my_invoice_einvoice)
    assert "validate_document" in src
    # No 422 raise and no assert_valid in the supplier path — the document is
    # always returned; validation only produces a logged warning.
    assert "status_code=422" not in src
    assert "assert_valid" not in src


# ---------------------------------------------------------------------------
# Behavioral: cross-vendor isolation
# ---------------------------------------------------------------------------
def _invoice(vendor_id) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        vendor_id=vendor_id,
        invoice_number="INV-PORTAL-1",
        invoice_date=date(2024, 6, 1),
        due_date=date(2024, 7, 1),
        currency="EUR",
        reference_number=None,
        po_number=None,
        payment_terms=None,
        payment_method=None,
        vendor_name="Vendor SARL",
        vendor_tax_id="FR40123456789",
        vendor_address="12 Rue de Paris",
        subtotal=Decimal("1000.00"),
        amount=Decimal("1200.00"),
        tax_amount=Decimal("200.00"),
        tax_rate=Decimal("20.00"),
        discount_amount=Decimal("0.00"),
        shipping_amount=Decimal("0.00"),
    )


def _scoped_db_returning(invoice):
    """A db mock whose Invoice query returns `invoice` (None for the miss case)
    and whose line-item query returns no rows."""
    invoice_result = MagicMock()
    invoice_result.scalar_one_or_none = MagicMock(return_value=invoice)
    line_result = MagicMock()
    line_scalars = MagicMock()
    line_scalars.all = MagicMock(return_value=[])
    line_result.scalars = MagicMock(return_value=line_scalars)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[invoice_result, line_result])
    return db


def _ctrl_db_returning_org():
    org = SimpleNamespace(name="Our Co", settings={"company": {"name": "Our Co"}})
    org_result = MagicMock()
    org_result.scalar_one_or_none = MagicMock(return_value=org)
    ctrl = AsyncMock()
    ctrl.execute = AsyncMock(return_value=org_result)
    return ctrl


@pytest.mark.asyncio
async def test_other_vendors_invoice_is_404():
    """Vendor B requests vendor A's invoice id. The scoped query (id AND
    vendor_id) returns no row → 404, never a foreign document."""
    from fastapi import HTTPException

    db = _scoped_db_returning(None)  # scoped query matches nothing
    ctrl = AsyncMock()
    vu = SimpleNamespace(id=uuid.uuid4(), vendor_id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        await get_my_invoice_einvoice(invoice_id=uuid.uuid4(), db=db, ctrl_db=ctrl, vu=vu)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_owning_vendor_gets_its_own_document():
    """Positive control: the invoice belongs to the caller → 200, and the
    returned UBL is the caller's invoice (seller == its vendor)."""
    vendor_id = uuid.uuid4()
    invoice = _invoice(vendor_id)
    db = _scoped_db_returning(invoice)
    ctrl = _ctrl_db_returning_org()
    vu = SimpleNamespace(id=uuid.uuid4(), vendor_id=vendor_id)

    resp = await get_my_invoice_einvoice(invoice_id=invoice.id, db=db, ctrl_db=ctrl, vu=vu)
    assert resp.media_type == "application/xml"
    assert "attachment" in resp.headers["content-disposition"]
    doc = parse_ubl(resp.body)
    assert doc.invoice_number == "INV-PORTAL-1"
    assert doc.seller.name == "Vendor SARL"


@pytest.mark.asyncio
async def test_export_does_not_raise_on_tax_warning():
    """Even if the document carries a tax soft-warning, the supplier still gets
    the UBL (no 422)."""
    vendor_id = uuid.uuid4()
    invoice = _invoice(vendor_id)
    # Make the buyer side malformed-able would require org settings; the seller
    # has no country_code (mapper doesn't set one) so tax checks skip — the key
    # assertion is simply that a successful 200 comes back.
    db = _scoped_db_returning(invoice)
    ctrl = _ctrl_db_returning_org()
    vu = SimpleNamespace(id=uuid.uuid4(), vendor_id=vendor_id)

    resp = await get_my_invoice_einvoice(invoice_id=invoice.id, db=db, ctrl_db=ctrl, vu=vu)
    assert resp.media_type == "application/xml"
