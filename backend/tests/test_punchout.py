"""Real-DB coverage for the punch-out catalog round-trip.

Covers the live cXML/OCI punch-out flow on top of the catalogs router:
``POST /catalogs/{id}/punchout/start`` (mock adapter → start URL + a pending
:class:`PunchoutSession`), the public secret-gated cart-return webhook
(``POST /catalogs/punchout/return/{slug}`` — BuyerCookie + HMAC match stores the
cart, every rejection is a silent 204), and
``POST /catalogs/punchout/sessions/{id}/convert`` (returned cart → requisition,
idempotent + row-locked). Plus RBAC and tenant isolation. Mirrors
``test_catalogs.py`` / ``test_peppol_inbound.py``. DO NOT RUN concurrently — the
``realdb`` fixture truncates shared tables; the orchestrator runs the suite
sequentially at the end.
"""

import hashlib
import hmac
import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.procurement import (
    Catalog,
    CatalogType,
    PunchoutSession,
    PunchoutSessionStatus,
    PurchaseRequisition,
)
from app.models.workflow import AuditLog

_DEV_SECRET = "dev-punchout-return-secret"  # matches .env.development


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _sign(body: bytes, secret: str = _DEV_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _cart_envelope(*, buyer_cookie: str, items: list[dict] | None = None, currency="USD") -> bytes:
    """Dev JSON cart envelope the mock adapter parses."""
    return json.dumps(
        {
            "buyer_cookie": buyer_cookie,
            "currency": currency,
            "items": items
            if items is not None
            else [
                {
                    "description": "Widget",
                    "sku": "W-1",
                    "quantity": "3",
                    "unit_price": "10.50",
                    "uom": "EA",
                }
            ],
        }
    ).encode("utf-8")


@pytest.fixture(autouse=True)
def _enable_punchout(monkeypatch):
    """Pin the return signing secret to the known dev value for every test
    (the provider is already `mock` by default)."""
    from app.config import settings

    monkeypatch.setattr(settings, "punchout_return_signing_secret", _DEV_SECRET)
    monkeypatch.setattr(settings, "punchout_provider", "mock")


async def _make_punchout_catalog(
    realdb, *, key="a", url="https://supplier.example/punchout"
) -> str:
    """Create a punch-out catalog (admin/manager setup) and return its id.

    Catalog *creation* is an admin/ap_manager action; the buyer roles
    (ap_clerk) only *shop* an existing punch-out catalog. So the catalog is
    always provisioned with a manager client, independent of whichever role
    drives the punch-out start/convert below.
    """
    async with realdb.client(key=key, role="ap_manager") as c:
        resp = await c.post(
            "/api/catalogs",
            json={"name": _uniq("PunchVendor"), "catalog_type": "punchout", "punchout_url": url},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# table existence
# ---------------------------------------------------------------------------


async def test_punchout_sessions_table_exists(realdb):
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        from sqlalchemy import text

        await s.execute(text("SELECT 1 FROM punchout_sessions LIMIT 1"))  # raises if missing


# ---------------------------------------------------------------------------
# start session
# ---------------------------------------------------------------------------


async def test_start_session_returns_url_and_persists_pending(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id

    cid = await _make_punchout_catalog(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/catalogs/{cid}/punchout/start")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["provider"] == "mock"
    assert body["buyer_cookie"].startswith("poc_")
    # The mock start URL is derived off the catalog's punchout_url.
    assert body["start_url"].startswith("https://supplier.example/punchout")
    assert body["buyer_cookie"] in body["start_url"]

    async with mk() as s:
        session = (
            await s.execute(
                select(PunchoutSession).where(PunchoutSession.id == uuid.UUID(body["session_id"]))
            )
        ).scalar_one()
        assert session.organization_id == org_id
        assert session.status == PunchoutSessionStatus.pending
        assert session.cart_total is None
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.entity_type == "punchout_session")
                )
            )
            .scalars()
            .all()
        )
        assert "punchout.session_started" in actions


async def test_start_on_non_punchout_catalog_422(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        cid = (
            await c.post("/api/catalogs", json={"name": _uniq("Int"), "catalog_type": "internal"})
        ).json()["id"]
        resp = await c.post(f"/api/catalogs/{cid}/punchout/start")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "catalog_not_punchout"


async def test_start_punchout_without_url_422(realdb):
    # A punch-out catalog with no URL fails closed with a PII-free code.
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    async with mk() as s:
        from app.tenant import resolve_default_entity_id

        entity_id = await resolve_default_entity_id(s)
        cat = Catalog(
            name=_uniq("NoUrl"),
            catalog_type=CatalogType.punchout,
            organization_id=org_id,
            entity_id=entity_id,
        )
        s.add(cat)
        await s.commit()
        cid = str(cat.id)

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/catalogs/{cid}/punchout/start")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "no_punchout_url"


# ---------------------------------------------------------------------------
# cart return (public, secret-gated)
# ---------------------------------------------------------------------------


async def test_return_stores_cart_on_buyer_cookie_match(realdb):
    mk = realdb.sessionmaker("a")
    slug = realdb.info("a").slug

    cid = await _make_punchout_catalog(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        start = (await c.post(f"/api/catalogs/{cid}/punchout/start")).json()
        cookie = start["buyer_cookie"]
        sid = start["session_id"]

    body = _cart_envelope(buyer_cookie=cookie)
    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(
            f"/api/catalogs/punchout/return/{slug}?buyer_cookie={cookie}",
            content=body,
            headers={"X-Punchout-Signature": _sign(body)},
        )
    assert resp.status_code == 204

    async with mk() as s:
        session = (
            await s.execute(select(PunchoutSession).where(PunchoutSession.id == uuid.UUID(sid)))
        ).scalar_one()
        assert session.status == PunchoutSessionStatus.returned
        assert session.returned_at is not None
        # Exact Decimal: 3 * 10.50 = 31.50.
        assert session.cart_total == Decimal("31.50")
        assert isinstance(session.cart_total, Decimal)
        assert len(session.cart_items) == 1
        assert session.cart_items[0]["sku"] == "W-1"

        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.action == "punchout.cart_returned")
                )
            )
            .scalars()
            .all()
        )
        assert len(actions) == 1


async def test_return_bad_signature_rejected_no_state_change(realdb):
    mk = realdb.sessionmaker("a")
    slug = realdb.info("a").slug

    cid = await _make_punchout_catalog(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        start = (await c.post(f"/api/catalogs/{cid}/punchout/start")).json()
        cookie = start["buyer_cookie"]
        sid = start["session_id"]

    body = _cart_envelope(buyer_cookie=cookie)
    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(
            f"/api/catalogs/punchout/return/{slug}?buyer_cookie={cookie}",
            content=body,
            headers={"X-Punchout-Signature": "deadbeef"},  # wrong signature
        )
    # Silent 204 — no enumeration.
    assert resp.status_code == 204

    async with mk() as s:
        session = (
            await s.execute(select(PunchoutSession).where(PunchoutSession.id == uuid.UUID(sid)))
        ).scalar_one()
        # Still pending — the bad signature changed nothing.
        assert session.status == PunchoutSessionStatus.pending
        assert session.cart_total is None


async def test_return_unknown_cookie_rejected(realdb):
    slug = realdb.info("a").slug
    body = _cart_envelope(buyer_cookie="poc_does_not_exist")
    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(
            f"/api/catalogs/punchout/return/{slug}",
            content=body,
            headers={"X-Punchout-Signature": _sign(body)},
        )
    # No matching session → silent 204.
    assert resp.status_code == 204


async def test_return_query_cookie_mismatch_rejected(realdb):
    mk = realdb.sessionmaker("a")
    slug = realdb.info("a").slug

    cid = await _make_punchout_catalog(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        start = (await c.post(f"/api/catalogs/{cid}/punchout/start")).json()
        cookie = start["buyer_cookie"]
        sid = start["session_id"]

    # The signed body's cookie disagrees with the query-string cookie → reject.
    body = _cart_envelope(buyer_cookie=cookie)
    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(
            f"/api/catalogs/punchout/return/{slug}?buyer_cookie=poc_other",
            content=body,
            headers={"X-Punchout-Signature": _sign(body)},
        )
    assert resp.status_code == 204

    async with mk() as s:
        session = (
            await s.execute(select(PunchoutSession).where(PunchoutSession.id == uuid.UUID(sid)))
        ).scalar_one()
        assert session.status == PunchoutSessionStatus.pending


# ---------------------------------------------------------------------------
# convert returned cart → requisition (idempotent, row-locked)
# ---------------------------------------------------------------------------


async def _start_and_return(realdb, *, role="ap_clerk") -> tuple[str, str]:
    """Drive start + cart-return; return (session_id, buyer_cookie)."""
    slug = realdb.info("a").slug
    cid = await _make_punchout_catalog(realdb)
    async with realdb.client(key="a", role=role) as c:
        start = (await c.post(f"/api/catalogs/{cid}/punchout/start")).json()
    cookie = start["buyer_cookie"]
    sid = start["session_id"]
    body = _cart_envelope(buyer_cookie=cookie)
    async with realdb.client(key="a", role=None) as c:
        resp = await c.post(
            f"/api/catalogs/punchout/return/{slug}?buyer_cookie={cookie}",
            content=body,
            headers={"X-Punchout-Signature": _sign(body)},
        )
        assert resp.status_code == 204
    return sid, cookie


async def test_convert_returned_session_creates_requisition(realdb):
    mk = realdb.sessionmaker("a")
    sid, _ = await _start_and_return(realdb)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/catalogs/punchout/sessions/{sid}/convert")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["total"] == 31.5
    req_id = body["requisition_id"]

    async with mk() as s:
        session = (
            await s.execute(select(PunchoutSession).where(PunchoutSession.id == uuid.UUID(sid)))
        ).scalar_one()
        assert session.status == PunchoutSessionStatus.converted
        assert session.converted_requisition_id == uuid.UUID(req_id)

        req = (
            await s.execute(
                select(PurchaseRequisition).where(PurchaseRequisition.id == uuid.UUID(req_id))
            )
        ).scalar_one()
        # Exact Decimal total carried from the cart.
        assert req.total == Decimal("31.50")
        assert isinstance(req.total, Decimal)

        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.action == "punchout.session_converted")
                )
            )
            .scalars()
            .all()
        )
        assert "punchout.session_converted" in actions


async def test_convert_is_idempotent(realdb):
    mk = realdb.sessionmaker("a")
    sid, _ = await _start_and_return(realdb)

    async with realdb.client(key="a", role="ap_manager") as c:
        first = (await c.post(f"/api/catalogs/punchout/sessions/{sid}/convert")).json()
        second = await c.post(f"/api/catalogs/punchout/sessions/{sid}/convert")

    assert second.status_code == 200
    sbody = second.json()
    # Replay returns the SAME requisition and flags created=False.
    assert sbody["created"] is False
    assert sbody["requisition_id"] == first["requisition_id"]

    async with mk() as s:
        count = (
            await s.execute(
                select(func.count())
                .select_from(PurchaseRequisition)
                .where(
                    PurchaseRequisition.id == uuid.UUID(first["requisition_id"]),
                )
            )
        ).scalar_one()
        assert count == 1


async def test_convert_pending_session_422(realdb):
    # A session that has not returned a cart cannot be converted.
    cid = await _make_punchout_catalog(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        start = (await c.post(f"/api/catalogs/{cid}/punchout/start")).json()
        sid = start["session_id"]
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/catalogs/punchout/sessions/{sid}/convert")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# RBAC + tenant isolation
# ---------------------------------------------------------------------------


async def test_cfo_read_only_cannot_start_or_convert(realdb):
    # CFO is read-only on punch-out mutations (buyers shop; cfo only reads).
    cid = await _make_punchout_catalog(realdb)
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post(f"/api/catalogs/{cid}/punchout/start")
    assert resp.status_code == 403


async def test_cfo_can_read_session(realdb):
    cid = await _make_punchout_catalog(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        sid = (await c.post(f"/api/catalogs/{cid}/punchout/start")).json()["session_id"]
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get(f"/api/catalogs/punchout/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == sid


async def test_tenant_isolation_session_not_visible_cross_tenant(realdb):
    cid = await _make_punchout_catalog(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        sid = (await c.post(f"/api/catalogs/{cid}/punchout/start")).json()["session_id"]
    async with realdb.client(key="b", role="ap_manager") as c:
        assert (await c.get(f"/api/catalogs/punchout/sessions/{sid}")).status_code == 404


# ---------------------------------------------------------------------------
# Boot guard — a deployed env (FEOH_DEBUG=false) may not run a live punch-out
# provider without a return-signing secret configured: `_verify_return_signature`
# falls back to `bool(settings.debug)` when the secret is empty, which fails
# closed today only because `settings.debug` defaults False — this guard makes
# a misconfigured live deploy fail loudly at boot instead of silently rejecting
# every supplier cart return. Mirrors the PEPPOL-inbound / email-intake /
# billing boot guards in `app/main.py::lifespan`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_refuses_live_provider_without_return_secret(monkeypatch):
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "secret_key", "a-real-non-default-secret-value")
    monkeypatch.setattr(settings, "punchout_provider", "cxml")
    monkeypatch.setattr(settings, "punchout_return_signing_secret", "")

    with pytest.raises(RuntimeError, match="FEOH_PUNCHOUT_RETURN_SIGNING_SECRET"):
        async with lifespan(object()):  # pragma: no cover - never enters body
            pass


@pytest.mark.asyncio
async def test_boot_allows_mock_provider_without_return_secret(monkeypatch):
    """The documented local-first default: mock provider + no secret (both
    defaults) must never trip the guard — a fresh `pnpm dev` clone boots fine."""
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "secret_key", "a-real-non-default-secret-value")
    monkeypatch.setattr(settings, "punchout_provider", "mock")
    monkeypatch.setattr(settings, "punchout_return_signing_secret", "")
    monkeypatch.setattr(settings, "extraction_reaper_enabled", False)

    async with lifespan(object()):
        pass


@pytest.mark.asyncio
async def test_boot_allows_live_provider_with_return_secret(monkeypatch):
    """A deployed env with a real provider + secret configured is unaffected."""
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "secret_key", "a-real-non-default-secret-value")
    monkeypatch.setattr(settings, "punchout_provider", "cxml")
    monkeypatch.setattr(settings, "punchout_return_signing_secret", _DEV_SECRET)
    monkeypatch.setattr(settings, "extraction_reaper_enabled", False)

    async with lifespan(object()):
        pass


# ---------------------------------------------------------------------------
# cXML PunchOutOrderMessage parsing — pure, no DB.
#
# A real supplier cart carries `Shipping`, `Tax`, `SpendDetail` and
# `Distribution` blocks as SIBLINGS of `ItemDetail` inside `ItemIn`, and each of
# them contains its own `<Money>` and `<Description>`. Price / description /
# UoM must therefore be read from `ItemDetail` specifically — scanning every
# descendant lets the LAST such block win, pricing the line off the tax figure.
# ---------------------------------------------------------------------------


def _cxml_cart(buyer_cookie: str, item_in_body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<cXML><Message><PunchOutOrderMessage>"
        f"<BuyerCookie>{buyer_cookie}</BuyerCookie>"
        "<PunchOutOrderMessageHeader/>"
        f"{item_in_body}"
        "</PunchOutOrderMessage></Message></cXML>"
    ).encode()


def test_cxml_price_comes_from_item_detail_not_a_trailing_tax_block():
    from app.services.punchout_adapters.cxml import parse_cxml_order_message

    body = _cxml_cart(
        "cookie-1",
        '<ItemIn quantity="10">'
        "<ItemID><SupplierPartID>SKU-1</SupplierPartID></ItemID>"
        "<ItemDetail>"
        '<UnitPrice><Money currency="USD">250.00</Money></UnitPrice>'
        "<Description>Laptop dock</Description>"
        "<UnitOfMeasure>EA</UnitOfMeasure>"
        "</ItemDetail>"
        # Legal cXML siblings, each carrying their own Money + Description.
        '<Shipping><Money currency="USD">15.00</Money>'
        "<Description>Ground</Description></Shipping>"
        '<Tax><Money currency="USD">200.00</Money>'
        "<Description>Sales tax</Description></Tax>"
        "</ItemIn>",
    )
    cart = parse_cxml_order_message(body)
    assert cart is not None
    assert len(cart.items) == 1
    item = cart.items[0]
    assert item.unit_price == Decimal("250.00")
    assert item.description == "Laptop dock"
    assert item.uom == "EA"
    assert item.sku == "SKU-1"
    assert item.quantity == Decimal("10")
    # 10 x 250.00 — never 10 x 200.00 (the tax figure).
    assert cart.total == Decimal("2500.00")


def test_cxml_plain_item_without_sibling_money_blocks_still_parses():
    from app.services.punchout_adapters.cxml import parse_cxml_order_message

    body = _cxml_cart(
        "cookie-2",
        '<ItemIn quantity="2">'
        "<ItemID><SupplierPartID>SKU-9</SupplierPartID></ItemID>"
        "<ItemDetail>"
        '<UnitPrice><Money currency="EUR">10.50</Money></UnitPrice>'
        "<Description>Widget</Description>"
        "</ItemDetail>"
        "</ItemIn>",
    )
    cart = parse_cxml_order_message(body)
    assert cart is not None
    assert cart.items[0].unit_price == Decimal("10.50")
    assert cart.items[0].currency == "EUR"
    assert cart.currency == "EUR"
    assert cart.total == Decimal("21.00")
