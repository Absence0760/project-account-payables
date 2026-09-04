"""An unreadable sanctions answer must never resolve to `allow`.

`ScreeningResult.result` is a three-value contract — `clear` | `match` |
`review_required`. `check_payment_compliance` branched on two of them and let
anything else fall through to the trailing
``verdict = "hold" if reasons else "allow"``, so a provider emitting a fourth
value **cleared the payment**. That is the one verdict that must never be
reached by omission: a sanctions gate that clears a name because it could not
read the answer is worse than one that never ran, because the payment goes out
carrying the audit row of a check that reported nothing.

The fix is an explicit ``elif screening.result != "clear"`` — `clear` is the
only value that proceeds silently, everything else adds a reason and holds.
`hold` and not `refuse` on purpose, matching the unknown-PROVIDER path: the
payment waits in `pending_compliance`, the caller opens the
`payment_compliance_hold` exception, and an operator can release it once the
adapter is understood. See `docs/decisions.md` §61 (and §36 for the provider
half).

What this file covers beyond the landed sweep:

* the recognised vocabulary keeps its three distinct verdicts;
* a wide unrecognised vocabulary — blank, whitespace, wrong case, trailing
  space, invented labels, and non-strings (`None` / `0` / `{}`) — every one of
  which must hold;
* a **structural drift guard**: the branch's handled vocabulary is read off the
  source, so adding a fourth handled verdict without re-checking the default
  path fails here, and a behavioural sweep proves `clear` is the only value in
  the whole space that allows;
* the `UnknownSanctionsProviderError` interaction (§36) — absorbed as a hold,
  never a 500 and never `clear`, and no screen is recorded as having run;
* the PII-free category taxonomy still reaching the decision reasons and the
  persisted row, including the case that matters most — adverse media turning a
  `clear` verdict into a hold;
* the hold observable where it counts, over real HTTP: the payment never
  reaches the processor adapter, the `payment_compliance_hold` exception opens,
  and `/compliance/release` re-runs the same gate rather than bypassing it.
"""

from __future__ import annotations

import inspect
import re
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor
from app.services import compliance as compliance_service
from app.services.compliance import check_payment_compliance
from app.services.payment_adapters.base import PaymentResult, PaymentStatus
from app.services.sanctions_adapters import (
    ScreeningResult,
    UnknownSanctionsProviderError,
    get_sanctions_adapter,
)
from app.services.sanctions_categories import RAW_RESPONSE_CATEGORIES_KEY

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

# The declared contract. `clear` is the ALLOW-list of one; the other two have
# their own explicit branches.
_DECLARED_RESULTS = ("clear", "match", "review_required")

# Values outside the contract. Case variants and a trailing space are included
# deliberately: the comparisons are exact, so a provider differing only in case
# is exactly as unreadable as one inventing a new word, and guessing at it is
# the failure being removed. Non-strings model a provider whose JSON shape we
# have never seen (the three live adapters are unkeyed skeletons today).
_UNRECOGNISED_RESULTS = [
    "",
    " ",
    "clear ",
    " clear",
    "Clear",
    "CLEAR",
    "MATCH",
    "match ",
    "Review_Required",
    "REVIEW_REQUIRED",
    "review",
    "escalate",
    "unknown",
    "pending",
    "error",
    "timeout",
    "no_hit",
    "false_positive",
    None,
    0,
    1,
    True,
    False,
    {},
    [],
]


class _FixedResultAdapter:
    """A sanctions adapter that returns exactly the `ScreeningResult` it is told
    to — including values outside the declared vocabulary, which no shipped
    adapter will ever produce."""

    provider_name = "fixture"

    def __init__(self, result, *, categories=(), matched_list=None, raw_response=None):
        self._result = result
        self._categories = tuple(categories)
        self._matched_list = matched_list
        self._raw_response = raw_response or {}
        self.calls = 0

    async def screen_vendor(self, **_kwargs):
        self.calls += 1
        return ScreeningResult(
            provider=self.provider_name,
            result=self._result,
            matched_list=self._matched_list,
            risk_score=Decimal("0.00"),
            raw_response=self._raw_response,
            categories=self._categories,
        )

    async def test_connection(self) -> bool:  # pragma: no cover - contract only
        return True


def _vendor(*, name="Acme GmbH", country="DE", kyc_status="verified", blocked=False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        tax_id=None,
        bank_details={"country": country} if country else None,
        kyc_status=kyc_status,
        beneficial_owner_data=None,
        payments_blocked=blocked,
        payments_blocked_reason=None,
    )


def _mock_db(trailing: Decimal = Decimal("0"), excluded: int = 0):
    """Mock session: the only query `check_payment_compliance` runs is the
    trailing-12m AML sum, consumed with `.one()`."""
    res = MagicMock()
    res.one = MagicMock(return_value=(trailing, excluded))
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    return db


async def _decide(result, *, categories=(), matched_list=None, raw_response=None, **overrides):
    """Run the gate against an adapter pinned to `result`.

    Uses the documented `sanctions_adapter=` injection point rather than a
    monkeypatch, so the test exercises the same code path production takes once
    the adapter is resolved.
    """
    adapter = _FixedResultAdapter(
        result, categories=categories, matched_list=matched_list, raw_response=raw_response
    )
    db = overrides.pop("db", None) or _mock_db()
    kwargs = {
        "vendor": _vendor(),
        "payment_amount": Decimal("100.00"),
        "payment_currency": "USD",
        "payment_method": "ach",
        "org_settings": {},
        "organization_id": uuid.uuid4(),
        **overrides,
    }
    decision = await check_payment_compliance(db, sanctions_adapter=adapter, **kwargs)
    return decision, adapter, db


def _added_sanctions_rows(db):
    return [call.args[0] for call in db.add.call_args_list]


# --------------------------------------------------------------------------- #
# The recognised vocabulary keeps its three distinct verdicts
# --------------------------------------------------------------------------- #


async def test_clear_is_the_only_value_that_proceeds_silently():
    decision, adapter, _ = await _decide("clear")
    assert decision.verdict == "allow"
    assert decision.reasons == []
    assert adapter.calls == 1


async def test_match_still_refuses_and_names_the_list_without_the_raw_payload():
    decision, _, _ = await _decide(
        "match",
        matched_list="OFAC_SDN",
        raw_response={"hit": {"dob": "1970-01-01", "passport": "X1234567"}},
    )
    assert decision.verdict == "refuse"
    combined = " ".join(decision.reasons)
    assert "OFAC_SDN" in combined
    # Provider match detail can embed DOB / passport / address — invariant #7.
    assert "1970-01-01" not in combined
    assert "X1234567" not in combined


async def test_review_required_still_holds_with_its_own_reason():
    """The new branch is an `elif`, so it must not have swallowed the
    `review_required` case or relabelled it as unreadable."""
    decision, _, _ = await _decide("review_required", matched_list="FATF_HIGH_RISK_IR")
    assert decision.verdict == "hold"
    assert any("review_required" in r for r in decision.reasons)
    assert not any("unrecognised result" in r for r in decision.reasons)


# --------------------------------------------------------------------------- #
# Everything outside the contract holds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("unknown", _UNRECOGNISED_RESULTS)
async def test_an_unrecognised_result_holds_and_never_allows(unknown):
    """The defect, closed: pre-fix every one of these matched neither branch and
    fell through to `allow`, so the payment left for the rail on the strength of
    an answer we could not read."""
    decision, _, _ = await _decide(unknown)
    assert decision.verdict == "hold", (
        f"screening result {unknown!r} produced {decision.verdict!r}; an unreadable "
        "sanctions answer must never resolve to allow"
    )
    assert decision.verdict != "allow"
    assert any("unrecognised result" in r for r in decision.reasons)


@pytest.mark.parametrize("unknown", _UNRECOGNISED_RESULTS)
async def test_an_unrecognised_result_is_a_hold_not_a_refuse(unknown):
    """`hold`, deliberately — same as the unknown-PROVIDER path (§36). A
    `refuse` would be a dead end: `/compliance/release` re-runs the same gate,
    so a refusal on an unreadable value could never be cleared by a human."""
    decision, _, _ = await _decide(unknown)
    assert decision.verdict != "refuse"
    assert decision.verdict == "hold"


async def test_the_unrecognised_reason_names_the_provider_and_the_value():
    """Actionable for whoever has to triage the hold: which adapter, and what it
    said."""
    decision, _, _ = await _decide("escalate")
    reason = next(r for r in decision.reasons if "unrecognised result" in r)
    assert "escalate" in reason
    assert "fixture" in reason
    assert "review_required" in reason  # states how it is being treated


async def test_the_unrecognised_reason_is_bounded_and_carries_no_provider_payload():
    """The value is bounded provider vocabulary rather than PII, but it reaches
    an operator-facing reason string, so it is truncated — a provider echoing an
    unbounded blob must not paste it into the payment record. The raw response
    (which CAN carry PII) never appears at all."""
    decision, _, _ = await _decide(
        "z" * 500,
        raw_response={"subject": {"name": "Jane Doe", "dob": "1970-01-01"}},
    )
    reason = next(r for r in decision.reasons if "unrecognised result" in r)
    assert len(reason) < 200
    assert "z" * 33 not in reason
    combined = " ".join(decision.reasons)
    assert "Jane Doe" not in combined
    assert "1970-01-01" not in combined


async def test_the_screen_is_still_recorded_when_the_answer_is_unreadable():
    """The screening call happened and an auditor must see it — the new branch
    sits after `db.add(sanctions_row)`, so the evidence lands either way, with
    the provider's own verbatim value on the row."""
    decision, _, db = await _decide("escalate", categories=("pep",))
    rows = _added_sanctions_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.result == "escalate"
    assert row.provider == "fixture"
    assert (row.raw_response or {}).get(RAW_RESPONSE_CATEGORIES_KEY) == ["pep"]
    assert decision.sanctions_check_row is row
    assert decision.screening_result is not None


async def test_an_unrecognised_result_composes_with_the_kyc_refusal():
    """Additive, and the stronger verdict wins: an unreadable screen on a
    corridor that also fails KYC refuses, citing both."""
    decision, _, _ = await _decide(
        "error",
        vendor=_vendor(kyc_status="pending"),
        payment_method="international_wire",
        payment_amount=Decimal("50000.00"),
    )
    assert decision.verdict == "refuse"
    combined = " ".join(decision.reasons)
    assert "unrecognised result" in combined
    assert "requires KYC" in combined


async def test_an_unrecognised_result_composes_with_the_aml_signal():
    """Two independent hold reasons stack rather than one masking the other."""
    decision, _, _ = await _decide(
        "error",
        db=_mock_db(trailing=Decimal("250000")),
        org_settings={"compliance": {"aml_spend_alert_threshold": "100000"}},
    )
    assert decision.verdict == "hold"
    combined = " ".join(decision.reasons)
    assert "unrecognised result" in combined
    assert "trailing 12-month spend" in combined


async def test_a_blocked_vendor_still_refuses_before_any_screening_runs():
    """The sticky block short-circuits ahead of the adapter — the fallthrough
    guard must not have moved that boundary."""
    adapter = _FixedResultAdapter("clear")
    decision = await check_payment_compliance(
        _mock_db(),
        vendor=_vendor(blocked=True),
        payment_amount=Decimal("100.00"),
        payment_currency="USD",
        payment_method="ach",
        org_settings={},
        organization_id=uuid.uuid4(),
        sanctions_adapter=adapter,
    )
    assert decision.verdict == "refuse"
    assert adapter.calls == 0


# --------------------------------------------------------------------------- #
# Drift guards — the verdict cannot be reached by omission again
# --------------------------------------------------------------------------- #


def test_the_branch_vocabulary_is_pinned_and_clear_is_the_only_allow_list():
    """Structural guard on the shape of the fix.

    `match` and `review_required` are matched by equality (each has its own
    verdict); `clear` is matched by INEQUALITY, which is what makes the default
    path a hold rather than an allow. If a fourth verdict is ever handled here,
    this fails — the default path must be re-checked at the same time, because
    the whole defect was a value reaching `allow` because nothing claimed it.
    """
    src = inspect.getsource(check_payment_compliance)
    compared = re.findall(r'screening\.result\s*(==|!=)\s*"([a-z_]+)"', src)
    equality = {value for op, value in compared if op == "=="}
    inequality = {value for op, value in compared if op == "!="}

    assert equality == {"match", "review_required"}, (
        f"handled-by-equality verdicts changed to {equality}; re-check that the "
        "default path still HOLDS rather than allows"
    )
    assert inequality == {"clear"}, (
        f"the allow-list is {inequality}, not {{'clear'}} — an unreadable result "
        "must not be able to proceed silently"
    )
    assert equality | inequality == set(_DECLARED_RESULTS)
    # The final verdict is still reason-driven, which is what turns the
    # fallthrough's appended reason into a hold.
    assert 'verdict = "hold" if reasons else "allow"' in src


def test_the_declared_contract_is_the_three_values_the_gate_handles():
    """The vocabulary lives in `ScreeningResult`'s docstring; the gate must
    handle exactly that set, so a fourth documented value can't ship with no
    branch."""
    doc = ScreeningResult.__doc__ or ""
    documented = {v for v in _DECLARED_RESULTS if f'"{v}"' in doc}
    assert documented == set(_DECLARED_RESULTS)


@pytest.mark.parametrize(
    "candidate",
    list(_DECLARED_RESULTS)
    + [
        "cleared",
        "clean",
        "no_match",
        "potential_match",
        "possible_match",
        "whitelisted",
        "approved",
        "ok",
        "pass",
        "green",
        "0",
        "none",
        "null",
    ],
)
async def test_clear_is_the_only_value_in_the_whole_space_that_allows(candidate):
    """Behavioural counterpart to the source guard, over the labels a future
    provider is most likely to invent — including several that *mean* clear
    (`cleared`, `no_match`, `ok`). Meaning it is not enough: the gate must not
    guess, so each of these holds."""
    decision, _, _ = await _decide(candidate)
    if candidate == "clear":
        assert decision.verdict == "allow"
    else:
        assert decision.verdict != "allow", (
            f"{candidate!r} resolved to allow; only the exact contract value 'clear' may proceed"
        )


async def test_every_non_clear_value_produces_at_least_one_reason():
    """The trailing verdict is `hold if reasons else allow`, so "holds" and
    "records why" are the same property here. A silent hold would also be a
    dead end for whoever has to triage it."""
    for candidate in [*_UNRECOGNISED_RESULTS, "review_required"]:
        decision, _, _ = await _decide(candidate)
        assert decision.reasons, f"{candidate!r} held with no stated reason"


# --------------------------------------------------------------------------- #
# Unknown PROVIDER (decisions §36) — absorbed as a hold, never a 500
# --------------------------------------------------------------------------- #


def test_the_dispatcher_refuses_a_named_provider_it_has_no_adapter_for():
    """The premise of the hold below: the dispatcher raises rather than
    substituting `mock`, which clears every name outside its fixture list."""
    with pytest.raises(UnknownSanctionsProviderError) as exc:
        get_sanctions_adapter({"provider": "worldcheck"})
    assert exc.value.provider == "worldcheck"


async def test_an_unconfigured_provider_still_resolves_the_local_first_mock():
    """Absence of config is not a misconfiguration — a fresh clone screens with
    the mock and an innocuous vendor is allowed. This is what makes the hold
    below specific to a NAMED unknown provider."""
    decision = await check_payment_compliance(
        _mock_db(),
        vendor=_vendor(name="Acme Widgets Ltd"),
        payment_amount=Decimal("100.00"),
        payment_currency="USD",
        payment_method="ach",
        org_settings={},
        organization_id=uuid.uuid4(),
    )
    assert decision.verdict == "allow"


async def test_an_unknown_provider_holds_and_never_reads_as_clear():
    """§36: no verdict is available, and the one thing we must not do is
    proceed. It is absorbed into a hold — not raised (a 500 on the money path),
    not `allow`, and not `refuse` (which no human could clear)."""
    db = _mock_db()
    decision = await check_payment_compliance(
        db,
        vendor=_vendor(),
        payment_amount=Decimal("100.00"),
        payment_currency="USD",
        payment_method="ach",
        org_settings={"compliance": {"sanctions": {"provider": "worldcheck"}}},
        organization_id=uuid.uuid4(),
    )
    assert decision.verdict == "hold"
    assert any("could not run" in r and "worldcheck" in r for r in decision.reasons)
    # No screen happened, so nothing may claim one did.
    assert decision.screening_result is None
    assert decision.sanctions_check_row is None
    db.add.assert_not_called()


async def test_an_absurd_provider_name_is_bounded_in_the_hold_reason():
    """An oversized settings value can't bloat the payment's failure reason (the
    error type caps the echo)."""
    decision = await check_payment_compliance(
        _mock_db(),
        vendor=_vendor(),
        payment_amount=Decimal("100.00"),
        payment_currency="USD",
        payment_method="ach",
        org_settings={"compliance": {"sanctions": {"provider": "q" * 500}}},
        organization_id=uuid.uuid4(),
    )
    assert decision.verdict == "hold"
    reason = next(r for r in decision.reasons if "could not run" in r)
    assert len(reason) < 200


async def test_the_unknown_provider_and_unreadable_result_paths_agree():
    """Two different ways of not getting an answer, deliberately the same
    verdict — so releasing either behaves identically for an operator."""
    unreadable, _, _ = await _decide("escalate")
    unknown_provider = await check_payment_compliance(
        _mock_db(),
        vendor=_vendor(),
        payment_amount=Decimal("100.00"),
        payment_currency="USD",
        payment_method="ach",
        org_settings={"compliance": {"sanctions": {"provider": "worldcheck"}}},
        organization_id=uuid.uuid4(),
    )
    assert unreadable.verdict == unknown_provider.verdict == "hold"


# --------------------------------------------------------------------------- #
# Category taxonomy still reaches the decision + the row
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "categories", [("sanctions",), ("pep",), ("high_risk_country",), ("sanctions", "pep")]
)
async def test_non_media_categories_ride_the_row_without_changing_the_verdict(categories):
    """A `clear` verdict with a non-media category set is still an allow — the
    taxonomy informs downstream risk scoring, it does not silently gate."""
    decision, _, db = await _decide("clear", categories=categories)
    assert decision.verdict == "allow"
    row = _added_sanctions_rows(db)[0]
    assert (row.raw_response or {}).get(RAW_RESPONSE_CATEGORIES_KEY) == list(categories)


async def test_adverse_media_turns_a_clear_verdict_into_a_hold():
    """The fail-closed case the taxonomy exists for: negative news with nothing
    on a formal list yet must not be auto-allowed. The reason is deliberately
    NOT nested under `review_required`."""
    decision, _, _ = await _decide("clear", categories=("adverse_media",))
    assert decision.verdict == "hold"
    assert any("adverse-media" in r for r in decision.reasons)


async def test_adverse_media_on_a_match_still_refuses_and_names_both_signals():
    decision, _, _ = await _decide(
        "match", categories=("sanctions", "adverse_media"), matched_list="OFAC_SDN"
    )
    assert decision.verdict == "refuse"
    combined = " ".join(decision.reasons)
    assert "OFAC_SDN" in combined
    assert "adverse-media" in combined


async def test_adverse_media_on_a_review_required_verdict_states_both_reasons():
    decision, _, _ = await _decide(
        "review_required", categories=("pep", "adverse_media"), matched_list="PEP_LIST"
    )
    assert decision.verdict == "hold"
    combined = " ".join(decision.reasons)
    assert "review_required" in combined
    assert "adverse-media" in combined


async def test_adverse_media_on_an_unrecognised_result_states_both_reasons():
    """The two guards compose: neither swallows the other, so the operator sees
    both that the answer was unreadable and that negative news was reported."""
    decision, _, db = await _decide("escalate", categories=("adverse_media",))
    assert decision.verdict == "hold"
    combined = " ".join(decision.reasons)
    assert "unrecognised result" in combined
    assert "adverse-media" in combined
    row = _added_sanctions_rows(db)[0]
    assert (row.raw_response or {}).get(RAW_RESPONSE_CATEGORIES_KEY) == ["adverse_media"]


async def test_the_adverse_media_reason_never_carries_the_match_detail():
    """Names the signal and the provider only — the article / person / DOB in
    the provider payload stays in the JSONB column (invariant #7)."""
    decision, _, _ = await _decide(
        "clear",
        categories=("adverse_media",),
        raw_response={"articles": [{"headline": "Jane Doe charged with fraud"}]},
    )
    combined = " ".join(decision.reasons)
    assert "Jane Doe" not in combined
    assert "headline" not in combined


# --------------------------------------------------------------------------- #
# The hold, observable end to end: the payment never reaches the rail
# --------------------------------------------------------------------------- #

TENANT = "a"


async def _seed_approved_invoice(mk, org_id, *, number: str, amount: str) -> uuid.UUID:
    """An approved invoice with a clean, KYC-verified vendor — so the ONLY thing
    that can hold the payment is the screening verdict under test."""
    async with mk() as s:
        vendor = Vendor(
            organization_id=org_id,
            name="Globex Industrial",
            status="active",
            kyc_status="verified",
        )
        s.add(vendor)
        await s.flush()
        invoice = Invoice(
            organization_id=org_id,
            invoice_number=number,
            vendor_name="Globex Industrial",
            vendor_id=vendor.id,
            amount=Decimal(amount),
            currency="USD",
            status=InvoiceStatus.approved,
            invoice_date=date.today(),
            correlation_id=uuid.uuid4(),
        )
        s.add(invoice)
        await s.commit()
        return invoice.id


async def _create_and_execute_run(client, exec_client, invoice_id: uuid.UUID) -> str:
    created = await client.post(
        "/api/payments/runs",
        json={"items": [{"invoice_id": str(invoice_id), "method": "ach"}]},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    executed = await exec_client.post(f"/api/payments/runs/{run_id}/execute")
    assert executed.status_code == 200, executed.text
    return run_id


def _rail_spy():
    """Spy standing in for the payment processor.

    `create_payment` is the money-moving call; a held payment must never reach
    it, so "not called" is the assertion that matters most in this file. It
    returns a real settled `PaymentResult` so the one test that DOES let a
    payment through exercises the ordinary settlement path.
    """
    adapter = MagicMock()
    adapter.provider_name = "mock"
    adapter.supported_methods = ["ach"]
    adapter.create_payment = AsyncMock(
        return_value=PaymentResult(
            success=True,
            status=PaymentStatus.completed,
            provider_payment_id="spy-provider-payment-id",
            reference="spy-reference",
        )
    )
    return adapter


def _unreadable_screening():
    return patch.object(
        compliance_service,
        "get_sanctions_adapter",
        return_value=_FixedResultAdapter("escalate"),
    )


async def test_an_unreadable_screen_holds_the_payment_before_the_rail(realdb):
    """End to end: the money-path consequence of §61.

    Pre-fix the gate returned `allow` for this response and the payment was
    handed to the processor. Now it stays `pending_compliance`, the adapter is
    never called, the invoice does not advance, and the hold surfaces as a
    `payment_compliance_hold` exception in the normal queue.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_approved_invoice(mk, org_id, number="CSF-001", amount="500.00")
    rail = _rail_spy()

    with (
        _unreadable_screening(),
        patch("app.api.payments.get_payment_adapter", return_value=rail),
    ):
        async with realdb.client(key=TENANT, role="admin") as admin_client:
            async with realdb.client(key=TENANT, role="ap_manager") as mgr_client:
                await _create_and_execute_run(admin_client, mgr_client, invoice_id)

    rail.create_payment.assert_not_called()

    async with mk() as s:
        payment = (
            await s.execute(select(Payment).where(Payment.invoice_id == invoice_id))
        ).scalar_one()
        assert payment.status == "pending_compliance"
        assert (payment.failure_reason or "").startswith("compliance_hold")
        assert "unrecognised result" in (payment.failure_reason or "")
        # Nothing was ever submitted anywhere.
        assert payment.provider is None
        assert payment.provider_payment_id is None
        assert payment.submitted_at is None

        invoice = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
        assert invoice.status == InvoiceStatus.approved

        exc = (
            await s.execute(
                select(APException).where(
                    APException.invoice_id == invoice_id,
                    APException.exception_type == "payment_compliance_hold",
                )
            )
        ).scalar_one()
        assert exc.status == "open"

    async with realdb.client(key=TENANT, role="admin") as c:
        listed = await c.get("/api/exceptions")
    assert listed.status_code == 200
    assert any(
        i["invoice_id"] == str(invoice_id) and i["exception_type"] == "payment_compliance_hold"
        for i in listed.json()["items"]
    )


async def test_releasing_a_still_unreadable_hold_re_runs_the_gate_and_stays_held(realdb):
    """`/compliance/release` re-runs the SAME compliance-then-adapter path, never
    a bypass — so while the adapter is still unreadable the payment stays held,
    the exception stays open, and no money moves. That is why the verdict is
    `hold` and not `refuse`: this door exists and can be walked through once the
    adapter is understood."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_approved_invoice(mk, org_id, number="CSF-002", amount="500.00")
    rail = _rail_spy()

    with (
        _unreadable_screening(),
        patch("app.api.payments.get_payment_adapter", return_value=rail),
    ):
        async with realdb.client(key=TENANT, role="admin") as admin_client:
            async with realdb.client(key=TENANT, role="ap_manager") as mgr_client:
                await _create_and_execute_run(admin_client, mgr_client, invoice_id)

            async with mk() as s:
                payment_id = (
                    await s.execute(select(Payment.id).where(Payment.invoice_id == invoice_id))
                ).scalar_one()

            released = await admin_client.post(f"/api/payments/{payment_id}/compliance/release")

    assert released.status_code == 200, released.text
    assert released.json()["status"] == "pending_compliance"
    rail.create_payment.assert_not_called()

    async with mk() as s:
        exc = (
            await s.execute(
                select(APException).where(
                    APException.invoice_id == invoice_id,
                    APException.exception_type == "payment_compliance_hold",
                )
            )
        ).scalar_one()
        assert exc.status == "open"


async def test_releasing_once_the_adapter_reports_clear_lets_the_payment_through(realdb):
    """The other side of the same door: with a readable `clear` the release
    settles through the normal path and resolves the exception. Proves the hold
    was a gate, not a dead end — and that the guard does not block a payment
    whose screen we CAN read."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_approved_invoice(mk, org_id, number="CSF-003", amount="500.00")
    rail = _rail_spy()

    with (
        _unreadable_screening(),
        patch("app.api.payments.get_payment_adapter", return_value=rail),
    ):
        async with realdb.client(key=TENANT, role="admin") as admin_client:
            async with realdb.client(key=TENANT, role="ap_manager") as mgr_client:
                await _create_and_execute_run(admin_client, mgr_client, invoice_id)

            async with mk() as s:
                payment_id = (
                    await s.execute(select(Payment.id).where(Payment.invoice_id == invoice_id))
                ).scalar_one()

        # The adapter is understood now and answers within the contract.
        with (
            patch.object(
                compliance_service,
                "get_sanctions_adapter",
                return_value=_FixedResultAdapter("clear"),
            ),
            patch("app.services.payment_erp_sync.dispatch_payment_sync", AsyncMock()),
        ):
            async with realdb.client(key=TENANT, role="admin") as admin_client:
                released = await admin_client.post(f"/api/payments/{payment_id}/compliance/release")

    assert released.status_code == 200, released.text
    assert released.json()["status"] != "pending_compliance"

    async with mk() as s:
        exc = (
            await s.execute(
                select(APException).where(
                    APException.invoice_id == invoice_id,
                    APException.exception_type == "payment_compliance_hold",
                )
            )
        ).scalar_one()
        assert exc.status == "resolved"
        assert exc.resolution == "released"


async def test_a_named_unknown_provider_holds_the_payment_end_to_end(realdb):
    """§36 through HTTP with no patching at all — one typo in
    `settings.compliance.sanctions.provider` holds the payment instead of
    screening the tenant's whole vendor book against nothing."""
    from app.models.organization import Organization

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    ctrl = realdb.control_sessionmaker()

    async with ctrl() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        original = dict(org.settings or {})
        org.settings = {
            **original,
            "compliance": {"sanctions": {"provider": "worldcheck"}},
        }
        await s.commit()

    try:
        invoice_id = await _seed_approved_invoice(mk, org_id, number="CSF-004", amount="500.00")
        rail = _rail_spy()
        with patch("app.api.payments.get_payment_adapter", return_value=rail):
            async with realdb.client(key=TENANT, role="admin") as admin_client:
                async with realdb.client(key=TENANT, role="ap_manager") as mgr_client:
                    await _create_and_execute_run(admin_client, mgr_client, invoice_id)

        rail.create_payment.assert_not_called()
        async with mk() as s:
            payment = (
                await s.execute(select(Payment).where(Payment.invoice_id == invoice_id))
            ).scalar_one()
            assert payment.status == "pending_compliance"
            assert "could not run" in (payment.failure_reason or "")
    finally:
        async with ctrl() as s:
            org = (
                await s.execute(select(Organization).where(Organization.id == org_id))
            ).scalar_one()
            org.settings = original
            await s.commit()
