"""Supplier-portal W-9 / W-8 tax-form upload + management.

These mirror the unit-level convention of ``test_supplier_portal.py`` /
``test_card_reveal_endpoint.py``: the handlers are called directly with a
mocked tenant ``db`` (only ``execute`` / ``commit`` / ``refresh`` are used),
and the storage layer is patched at its source module. They lock down the
security-critical invariants for this feature:

  * vendor scoping — every handler resolves the vendor by ``vu.vendor_id``
    only, so vendor A can never read / download / write vendor B's form
    (cross-vendor → 404, not 403, no enumeration);
  * auth — every route declares ``get_current_vendor_user`` (asserted by
    ``test_supplier_portal.py``'s coverage gate, re-asserted here for this
    feature's routes);
  * PII — the audit row written on upload carries the form type + filename
    only, never the tax ID;
  * content-type rejection at the storage boundary.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import portal
from app.api.portal import (
    get_my_tax_form,
    get_my_tax_form_file,
    upload_my_tax_form,
)


def _vendor(**overrides) -> SimpleNamespace:
    org_id = overrides.pop("organization_id", uuid.uuid4())
    base = dict(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Acme Supplies",
        w9_file_key=None,
        w9_received_date=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _db_returning(vendor) -> MagicMock:
    """A tenant-DB mock whose single ``execute`` returns ``vendor`` via
    ``scalar_one_or_none``. Matches what the tax-form handlers issue (one
    ``select(Vendor)``)."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=vendor)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _vu(vendor) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), vendor_id=vendor.id)


class _FakeUpload:
    def __init__(self, content: bytes, content_type: str, filename: str):
        self._content = content
        self.content_type = content_type
        self.filename = filename

    async def read(self) -> bytes:
        return self._content


# ---------- auth-coverage gate (this feature's routes) ---------------------


def test_tax_form_routes_use_vendor_auth():
    """Re-assert the portal-wide invariant for the three new tax-form routes:
    each must inject ``get_current_vendor_user``."""
    import inspect

    paths = {
        "/portal/company/tax-form",
        "/portal/company/tax-form/file",
    }
    seen = set()
    for route in portal.router.routes:
        if route.path not in paths:
            continue
        seen.add(route.path)
        sig = inspect.signature(route.endpoint)
        has_vendor_dep = any(
            getattr(getattr(p.default, "dependency", None), "__name__", "")
            == "get_current_vendor_user"
            for p in sig.parameters.values()
        )
        assert has_vendor_dep, f"{route.path} ({route.endpoint.__name__}) missing vendor auth"
    assert paths <= seen, f"missing tax-form routes: {paths - seen}"


# ---------- form-type recovery from the stored key ------------------------


def test_form_type_recovered_from_key():
    org, ven = uuid.uuid4(), uuid.uuid4()
    assert portal._tax_form_type_from_key(f"{org}/tax-forms/{ven}/w8/form.pdf") == "w8"
    assert portal._tax_form_type_from_key(f"{org}/tax-forms/{ven}/w9/form.pdf") == "w9"
    # AP-side W-9 upload key (no form-type segment) → defaults to w9.
    assert portal._tax_form_type_from_key(f"{org}/w9/{ven}/form.pdf") == "w9"
    assert portal._tax_form_type_from_key(None) is None


# ---------- GET status -----------------------------------------------------


@pytest.mark.asyncio
async def test_get_tax_form_none_on_file():
    vendor = _vendor()
    resp = await get_my_tax_form(db=_db_returning(vendor), vu=_vu(vendor))
    assert resp.on_file is False
    assert resp.form_type is None
    assert resp.received_date is None


@pytest.mark.asyncio
async def test_get_tax_form_on_file():
    org = uuid.uuid4()
    ven = uuid.uuid4()
    vendor = _vendor(
        id=ven,
        organization_id=org,
        w9_file_key=f"{org}/tax-forms/{ven}/w8/w8ben.pdf",
        w9_received_date=date(2026, 1, 15),
    )
    resp = await get_my_tax_form(db=_db_returning(vendor), vu=_vu(vendor))
    assert resp.on_file is True
    assert resp.form_type == "w8"
    assert resp.received_date == date(2026, 1, 15)


# ---------- upload happy path ---------------------------------------------


@pytest.mark.asyncio
async def test_upload_tax_form_happy_path():
    org = uuid.uuid4()
    vendor = _vendor(organization_id=org)
    db = _db_returning(vendor)
    vu = _vu(vendor)
    up = _FakeUpload(b"%PDF-1.4 signed w9", "application/pdf", "w9.pdf")

    captured: dict = {}

    async def _fake_upload(org_id, vendor_id, form_type, file):
        captured["args"] = (org_id, vendor_id, form_type)
        key = f"{org_id}/tax-forms/{vendor_id}/{form_type}/w9.pdf"
        return key, "/api/portal/company/tax-form/file"

    async def _fake_audit(*_a, **kw):
        captured["audit"] = kw

    with (
        patch.object(portal, "upload_tax_form_file", _fake_upload),
        patch.object(portal, "dispatch_audit", _fake_audit),
    ):
        resp = await upload_my_tax_form(file=up, form_type="w9", db=db, vu=vu)

    # Wrote onto the caller's OWN vendor row only.
    assert captured["args"] == (org, vendor.id, "w9")
    assert vendor.w9_file_key.endswith("w9.pdf")
    assert vendor.w9_received_date == date.today()
    assert resp.on_file is True
    assert resp.form_type == "w9"
    db.commit.assert_awaited_once()

    # PII guard: audit details carry form type + filename, never a tax id.
    details = captured["audit"]["details"]
    assert details["form_type"] == "w9"
    assert details["filename"] == "w9.pdf"
    assert details["actor_type"] == "vendor_user"
    assert "tax_id" not in details
    assert captured["audit"]["action"] == "vendor.tax_form_uploaded_by_vendor"
    # Vendor user is not a control-plane user — actor_id stays None.
    assert captured["audit"]["actor_id"] is None


@pytest.mark.asyncio
async def test_upload_tax_form_w8_for_foreign_vendor():
    org = uuid.uuid4()
    vendor = _vendor(organization_id=org)
    db = _db_returning(vendor)
    up = _FakeUpload(b"%PDF w8", "application/pdf", "w8ben.pdf")

    async def _fake_upload(org_id, vendor_id, form_type, file):
        return f"{org_id}/tax-forms/{vendor_id}/{form_type}/w8ben.pdf", "/x"

    with (
        patch.object(portal, "upload_tax_form_file", _fake_upload),
        patch.object(portal, "dispatch_audit", AsyncMock()),
    ):
        resp = await upload_my_tax_form(file=up, form_type="w8", db=db, vu=_vu(vendor))
    assert resp.form_type == "w8"


@pytest.mark.asyncio
async def test_upload_tax_form_rejects_unknown_form_type():
    vendor = _vendor()
    up = _FakeUpload(b"x", "application/pdf", "x.pdf")
    with pytest.raises(HTTPException) as exc:
        await upload_my_tax_form(
            file=up, form_type="w-bogus", db=_db_returning(vendor), vu=_vu(vendor)
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_tax_form_rejects_bad_content_type():
    """The storage layer's content-type gate surfaces as a 400 — a .docx /
    arbitrary type can't be stored as a tax form."""
    org = uuid.uuid4()
    vendor = _vendor(organization_id=org)
    up = _FakeUpload(b"x", "application/msword", "memo.doc")
    # Patch the S3 client so the real storage helper runs its content-type gate
    # without touching MinIO; the gate must raise ValueError -> handler 400.
    from app.services import storage

    with (
        patch.object(storage, "_get_client", MagicMock()),
        patch.object(storage, "_ensure_bucket", MagicMock()),
        patch.object(portal, "dispatch_audit", AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc:
            await upload_my_tax_form(
                file=up, form_type="w9", db=_db_returning(vendor), vu=_vu(vendor)
            )
    assert exc.value.status_code == 400
    # The vendor row must NOT have been mutated on a rejected upload.
    assert vendor.w9_file_key is None


# ---------- download proxy + cross-vendor / cross-tenant isolation ---------


@pytest.mark.asyncio
async def test_download_tax_form_own_file():
    org = uuid.uuid4()
    ven = uuid.uuid4()
    key = f"{org}/tax-forms/{ven}/w9/w9.pdf"
    vendor = _vendor(id=ven, organization_id=org, w9_file_key=key)
    with patch.object(portal, "get_file", MagicMock(return_value=(b"PDFBYTES", "application/pdf"))):
        resp = await get_my_tax_form_file(db=_db_returning(vendor), vu=_vu(vendor))
    assert resp.body == b"PDFBYTES"
    assert resp.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_download_tax_form_404_when_none():
    vendor = _vendor(w9_file_key=None)
    with pytest.raises(HTTPException) as exc:
        await get_my_tax_form_file(db=_db_returning(vendor), vu=_vu(vendor))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_download_tax_form_rejects_cross_tenant_key():
    """Defence in depth: even if a vendor row somehow carried a key whose
    leading org segment isn't the vendor's org, the proxy 404s rather than
    serving another tenant's bytes."""
    vendor = _vendor(
        organization_id=uuid.uuid4(),
        w9_file_key=f"{uuid.uuid4()}/tax-forms/{uuid.uuid4()}/w9/secret.pdf",
    )
    with patch.object(portal, "get_file", MagicMock()) as gf:
        with pytest.raises(HTTPException) as exc:
            await get_my_tax_form_file(db=_db_returning(vendor), vu=_vu(vendor))
    assert exc.value.status_code == 404
    # We never even reached out to storage for a foreign-prefixed key.
    gf.assert_not_called()


@pytest.mark.asyncio
async def test_cross_vendor_probe_returns_404_everywhere():
    """Vendor B authenticates; their vendor row simply doesn't resolve (the
    query is scoped to ``vu.vendor_id``), so every handler 404s — the same
    "doesn't exist / not yours" conflation the rest of the portal uses. This
    is the cross-vendor isolation guarantee: A can't read, download, or write
    to B's form."""
    vu = SimpleNamespace(id=uuid.uuid4(), vendor_id=uuid.uuid4())
    db = _db_returning(None)  # no vendor row resolves for this caller

    with pytest.raises(HTTPException) as exc:
        await get_my_tax_form(db=db, vu=vu)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await get_my_tax_form_file(db=db, vu=vu)
    assert exc.value.status_code == 404

    up = _FakeUpload(b"x", "application/pdf", "x.pdf")
    with patch.object(portal, "dispatch_audit", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await upload_my_tax_form(file=up, form_type="w9", db=db, vu=vu)
    assert exc.value.status_code == 404


# ---------- source-level vendor-scoping assertion -------------------------


def test_tax_form_handlers_scope_by_vendor_id():
    import inspect

    for fn in (get_my_tax_form, upload_my_tax_form, get_my_tax_form_file):
        src = inspect.getsource(fn)
        assert "Vendor.id == vu.vendor_id" in src, f"{fn.__name__} not scoped to caller's vendor"
