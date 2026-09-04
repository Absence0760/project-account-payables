"""A NAMED provider with no registered adapter is refused — never the fixture.

`decisions.md` §29 removed the `_REGISTRY.get(x) or _REGISTRY["mock"]` fallback
from the payment / ERP / FX dispatchers, and §36 from sanctions. The reasoning
is the same everywhere and does not depend on the domain: a `mock` adapter is
**not an inert stub**. It is the thing that makes `pnpm dev` work with no cloud
account, so it answers *yes* to everything — and substituting it for a name we
do not recognise converts a configuration error into a confident wrong answer
that nothing anywhere reports.

Four registries still fell open. Two were closed in round 15 (cards, positive
pay); this file adds the other two and pins all four together, plus the drift
guard that stops a fifth appearing.

* **`card_adapters`** — `create_card` returns `success=True` with the fixture
  PAN `4242…`, so a `VirtualCard` row landed, the payment was marked
  `completed`, and the vendor was emailed a reveal link to that fixture PAN.
* **`tax_filing_adapters`** — `submit_batch` returns `accepted` with a
  `MOCK-<year>-<hex>` confirmation, so a `Tax1099Filing` row plus a
  `tax_1099.filed` audit row told the org its 1099s were e-filed when nothing
  reached the IRS — and the idempotency slot was burned, making the corrected
  retry a no-op.
* **`tin_validation_adapters`** — `validate` checks digit grouping and the IRS
  structural rules and nothing else, yet `Vendor.tin_verified_at` was stamped
  from it, driving B-notice / 24% backup-withholding decisions off a regex.
* **`positive_pay_adapters`** — renders the `csv` layout, stored under the
  REQUESTED format on the row and the audit trail with the
  `(run, bank_format)` slot burned. The bank cannot parse it, so the
  cheque-fraud control is simply not in force and nothing says so.

**The caller decides what the refusal means** (§29's per-caller table). This
file pins the callers for the two registries it converts, plus the positive-pay
route (which had the refusal but no route-level coverage of it):

  | call site | on refusal |
  |---|---|
  | `POST /api/tax/1099/file` | 409 — no filing row, no confirmation, slot free |
  | `POST /api/tax/vendors/{id}/tin-verify` | 409 — `tin_verified_at` untouched |
  | `POST /api/positive-pay/ach-authorization` | 422 — no file, no row |

The card call sites (all six of them) are pinned in
`tests/test_card_provider_resolution.py`; they are not duplicated here.

An **absent or empty** provider still resolves the local-first default in every
one of the four — guard rail 7. That is a normal state, not a misconfiguration.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.models.positive_pay import PositivePayFile
from app.models.tax_filing import Tax1099Filing
from app.models.vendor import Vendor
from app.services.card_adapters import UnknownCardProviderError, get_card_adapter
from app.services.card_adapters.lithic import LithicAdapter
from app.services.positive_pay_adapters import (
    UnknownPositivePayFormatError,
    get_positive_pay_formatter,
)
from app.services.positive_pay_adapters.csv_formatter import CsvPositivePayFormatter
from app.services.tax_filing_adapters import (
    UnknownTaxFilingProviderError,
    get_tax_filing_adapter,
)
from app.services.tax_filing_adapters.mock_adapter import MockTaxFilingAdapter
from app.services.tin_validation_adapters import (
    UnknownTinValidationProviderError,
    get_tin_validation_adapter,
)
from app.services.tin_validation_adapters.mock_adapter import MockTINValidationAdapter

TENANT = "a"
SERVICES_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"


# --------------------------------------------------------------------------- #
# (a) + (b) — the four registries, resolver-level
# --------------------------------------------------------------------------- #

#: ``(label, resolve, empty_config, expected_default_cls, named_unknown_config,
#:   error_cls)``. ``empty_config`` is what an org that has configured nothing
#: sends; it must still resolve the local-first default.
REGISTRY_CASES = [
    pytest.param(
        get_card_adapter,
        {"region": "US"},
        LithicAdapter,
        {"provider": "marqeta", "region": "US"},
        UnknownCardProviderError,
        id="card_adapters",
    ),
    pytest.param(
        get_tax_filing_adapter,
        None,
        MockTaxFilingAdapter,
        {"provider": "taxbandits"},
        UnknownTaxFilingProviderError,
        id="tax_filing_adapters",
    ),
    pytest.param(
        get_tin_validation_adapter,
        None,
        MockTINValidationAdapter,
        {"provider": "irs_direct"},
        UnknownTinValidationProviderError,
        id="tin_validation_adapters",
    ),
    pytest.param(
        get_positive_pay_formatter,
        None,
        CsvPositivePayFormatter,
        "wells_fargo_xyz",
        UnknownPositivePayFormatError,
        id="positive_pay_adapters",
    ),
]


@pytest.mark.parametrize(
    "resolve,empty_config,default_cls,unknown_config,error_cls", REGISTRY_CASES
)
def test_absent_config_still_resolves_the_local_first_default(
    resolve, empty_config, default_cls, unknown_config, error_cls
):
    """Guard rail 7: a fresh clone with no provider configured must work."""
    assert isinstance(resolve(empty_config), default_cls)


@pytest.mark.parametrize(
    "resolve,empty_config,default_cls,unknown_config,error_cls", REGISTRY_CASES
)
def test_named_unknown_provider_raises_instead_of_the_fixture(
    resolve, empty_config, default_cls, unknown_config, error_cls
):
    with pytest.raises(error_cls):
        resolve(unknown_config)


@pytest.mark.parametrize(
    "resolve,empty_config,default_cls,unknown_config,error_cls", REGISTRY_CASES
)
def test_the_bad_name_is_bounded_in_the_error(
    resolve, empty_config, default_cls, unknown_config, error_cls
):
    """The name is admin/caller-supplied config, not PII — but it reaches a log
    line and an HTTP body, so an absurd value must not bloat either."""
    absurd = "z" * 500
    if isinstance(unknown_config, dict):
        oversized = {**unknown_config, "provider": absurd}
    else:
        oversized = absurd
    with pytest.raises(error_cls) as exc:
        resolve(oversized)
    assert len(str(exc.value)) < 400


def test_the_registries_populate_themselves_before_refusing():
    """The refusal is only trustworthy if every built-in adapter has had a
    chance to register — a real provider a caller never imported must resolve,
    not raise. Both resolvers import their built-ins on the way in."""
    assert get_tax_filing_adapter({"provider": "tax1099"}).provider_name == "tax1099"
    assert get_tin_validation_adapter({"provider": "tax1099"}).provider_name == "tax1099"


# --------------------------------------------------------------------------- #
# The drift guard — a new registry cannot quietly fail open
# --------------------------------------------------------------------------- #

#: Dispatchers that refuse a NAMED unregistered provider. Adding a registry
#: means adding it here (and making it refuse) or to the list below with a
#: reason.
FAIL_CLOSED_DISPATCHERS = {
    "audit_shipping",
    "card_adapters",
    "enrichment_adapters",
    "erp_adapters",
    "extraction_adapters",
    "financing_adapters",
    "fx_adapters",
    "payment_adapters",
    "positive_pay_adapters",
    "sanctions_adapters",
    "tax_filing_adapters",
    "tin_validation_adapters",
}

#: Dispatchers that still resolve an unknown NAMED provider to their default.
#: Listed with what the fallback actually does, so the next reviewer can judge
#: it rather than inherit an assertion. These are **not** blessed — they are the
#: remainder of the same §29 sweep, tracked in `docs/followups.md`.
FAIL_OPEN_DISPATCHERS = {
    "assistant": (
        "claude-without-a-key → mock is the documented local-first downgrade; a "
        "typo'd name degrades to a deterministic fixture chat answer. Read-only, "
        "no money, nothing persisted claiming otherwise."
    ),
    "billing_adapters": (
        "falls back to mock, but the only path where that is dangerous — the "
        "PUBLIC billing webhook, whose mock parse_webhook verifies no signature "
        "— is refused at boot when the provider is mock or unregistered."
    ),
    "chat_notification_adapters": (
        "an approval notification degrades to the no-network mock. Best-effort "
        "by design; the transition never depends on it."
    ),
    "email_adapters": "falls back to `console`, which logs instead of sending.",
    "embedding_adapters": "falls back to mock vectors — RAG / duplicate-similarity quality only.",
    "peppol_adapters": "UNREVIEWED — a mock Access Point 'transmits' an e-invoice nowhere.",
    "punchout_adapters": "UNREVIEWED — a mock supplier returns a fixture cart.",
    "qms_adapters": "UNREVIEWED — mock inspection fixtures feed the 4-way match quality gate.",
    "tax_rate_adapters": "UNREVIEWED — a mock rate table drives VAT / GST computation.",
}


def _dispatcher_stems() -> set[str]:
    return {p.parent.name for p in SERVICES_DIR.glob("*/dispatcher.py")}


def test_every_adapter_registry_is_classified():
    """A new adapter family must declare whether it fails closed. Without this
    the §29 rule is a habit rather than a property — the four this file fixes
    were each written *after* §29 landed."""
    on_disk = _dispatcher_stems()
    classified = FAIL_CLOSED_DISPATCHERS | set(FAIL_OPEN_DISPATCHERS)
    unclassified = on_disk - classified
    assert not unclassified, (
        "new adapter registry with no fail-closed decision recorded: "
        f"{sorted(unclassified)} — make its resolver raise on a NAMED unknown "
        "provider and add it to FAIL_CLOSED_DISPATCHERS (decisions.md §29), or "
        "add it to FAIL_OPEN_DISPATCHERS with what its fallback actually does."
    )
    assert not classified - on_disk, (
        f"classified registry no longer on disk: {sorted(classified - on_disk)}"
    )


def _fallback_offenders(path: pathlib.Path) -> list[str]:
    """Every `<registry>.get(x) or <anything>` / `<registry>.get(x, <default>)`
    in `path`. Pure AST — no import, no execution."""
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders: list[str] = []

    def _is_registry_get(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and "REGISTRY" in node.func.value.id.upper()
        )

    for node in ast.walk(tree):
        # `_REGISTRY.get(provider) or _REGISTRY["mock"]`
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if any(_is_registry_get(v) for v in node.values[:-1]):
                offenders.append(f"{path.parent.name}:{node.lineno} (`.get(...) or <default>`)")
        # `_REGISTRY.get(provider, MockAdapter)`
        elif _is_registry_get(node) and len(node.args) > 1:
            offenders.append(f"{path.parent.name}:{node.lineno} (`.get(..., <default>)`)")
    return offenders


def test_no_fail_closed_dispatcher_reintroduces_the_fallback():
    """The behavioural tests above cover four resolvers; this covers all twelve,
    including the ones §29 and §36 fixed, so the shape cannot creep back in
    under a name this file does not import."""
    offenders: list[str] = []
    for stem in sorted(FAIL_CLOSED_DISPATCHERS):
        offenders.extend(_fallback_offenders(SERVICES_DIR / stem / "dispatcher.py"))

    assert not offenders, (
        "a fail-closed adapter registry resolves an unknown provider to a "
        "default again — the mock is not an inert stub (decisions.md §29): " + ", ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# (c) — the callers
# --------------------------------------------------------------------------- #


async def _set_provider(realdb, path: list[str], provider: str | None):
    """Point one `Organization.settings` provider at `provider` (None clears)."""
    org_id = realdb.info(TENANT).org_id
    async with realdb.control_sessionmaker()() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings = dict(org.settings or {})
        cursor = settings
        for key in path[:-1]:
            cursor[key] = dict(cursor.get(key) or {})
            cursor = cursor[key]
        if provider is None:
            cursor.pop(path[-1], None)
        else:
            cursor[path[-1]] = {"provider": provider}
        org.settings = settings
        await s.commit()


async def test_filing_route_409s_and_persists_no_filing(realdb):
    """The org named a partner we have no adapter for. Nothing may be recorded:
    a `Tax1099Filing` row would tell the operator their 1099s were transmitted,
    and it would hold the idempotency slot their corrected retry needs."""
    await _set_provider(realdb, ["tax", "filing"], "taxbandits")
    org_id = realdb.info(TENANT).org_id

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.post(
            "/api/tax/1099/file", json={"year": 2026, "idempotency_key": "unregistered-provider"}
        )

    assert resp.status_code == 409, resp.text
    assert "taxbandits" in resp.text
    assert "mock" in resp.text  # names the registered alternatives

    async with realdb.sessionmaker(TENANT)() as s:
        rows = (
            (await s.execute(select(Tax1099Filing).where(Tax1099Filing.organization_id == org_id)))
            .scalars()
            .all()
        )
    assert rows == [], "a filing row was persisted for a partner that was never reached"


async def test_filing_route_still_files_with_no_provider_configured(realdb):
    """The refusal must not cost the local-first default."""
    await _set_provider(realdb, ["tax", "filing"], None)

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.post(
            "/api/tax/1099/file", json={"year": 2026, "idempotency_key": "default-provider"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["provider"] == "mock"


async def test_tin_route_409s_and_leaves_the_verification_stamp_alone(realdb):
    """`tin_verified_at` is what the 1099 dashboard reads as "TIN verified" and
    what B-notice / backup-withholding decisions key off. An unresolvable
    provider must leave it — and the stored TIN — exactly as they were."""
    org_id = realdb.info(TENANT).org_id
    stamped = datetime(2026, 1, 2, tzinfo=UTC)
    async with realdb.sessionmaker(TENANT)() as s:
        v = Vendor(
            organization_id=org_id,
            name="Unresolvable Provider Co",
            tax_id="12-3456789",
            is_1099_eligible=True,
            tin_verified_at=stamped,
        )
        s.add(v)
        await s.commit()
        await s.refresh(v)
        vendor_id = v.id

    await _set_provider(realdb, ["tax", "tin_validation"], "irs_direct")

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.post(
            f"/api/tax/vendors/{vendor_id}/tin-verify", json={"tax_id": "98-7654321"}
        )

    assert resp.status_code == 409, resp.text
    assert "irs_direct" in resp.text
    # The refusal body carries a config name, never a TIN.
    assert "987654321" not in resp.text
    assert "98-7654321" not in resp.text

    async with realdb.sessionmaker(TENANT)() as s:
        row = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert row.tin_verified_at is not None, "an unresolvable provider cleared the stamp"
        assert row.tin_verified_at.replace(tzinfo=UTC) == stamped
        assert row.tax_id == "12-3456789", "the submitted TIN was written despite the refusal"


async def test_positive_pay_route_422s_and_writes_no_file(realdb):
    """A layout we cannot render must not become a CSV filed under the bank's
    name — the operator would believe the cheque-fraud control is in force."""
    await _set_provider(realdb, ["tax", "filing"], None)  # unrelated; keeps settings shaped
    org_id = realdb.info(TENANT).org_id

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.post(
            "/api/positive-pay/ach-authorization", json={"bank_format": "wells_fargo_xyz"}
        )

    assert resp.status_code == 422, resp.text
    assert "wells_fargo_xyz" in resp.text
    assert "csv" in resp.text  # names the registered alternatives

    async with realdb.sessionmaker(TENANT)() as s:
        rows = (
            (
                await s.execute(
                    select(PositivePayFile).where(PositivePayFile.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
    assert rows == [], "a positive-pay row was persisted for a layout we never rendered"


async def test_positive_pay_route_still_renders_the_default_layout(realdb):
    """The refusal must not cost the local-first default."""
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.post("/api/positive-pay/ach-authorization", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["bank_format"] == "csv"
