"""Coverage for the Adaptive AI Workflows first slice.

Two tiers (mirrors the repo):

  * Pure-Python edges — the approval-pattern aggregates, the σ/median-multiple
    anomaly guard, and the auto-approve-threshold derivation. These pin the
    deterministic (no-LLM, no-cloud-key) statistics without a DB.

  * Real-Postgres end-to-end (``realdb``) — drives the four ``/api/adaptive/*``
    routes against a live tenant DB so the audit_log ⋈ invoices join, the
    write-on-GET suggestion upsert, durable dismissal, the stale transition,
    RBAC, and tenant isolation are all real.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.adaptive_workflows import (
    ApproverPattern,
    EligibleApprover,
    VendorBaseline,
    _stdev,
    compute_approver_patterns,
    compute_vendor_baseline,
    compute_vendor_patterns,
    derive_suggestions,
    detect_invoice_anomaly,
    recommend_approvers,
)

# ---------------------------------------------------------------------------
# Pure-function unit tests (no DB)
# ---------------------------------------------------------------------------


def _decision(
    *,
    approver_id=None,
    vendor_id=None,
    vendor_name="V",
    amount="0",
    decision="approved",
    unmodified=True,
    ttd=None,
):
    return {
        "approver_id": approver_id,
        "vendor_id": vendor_id,
        "vendor_name": vendor_name,
        "amount": Decimal(amount),
        "decision": decision,
        "unmodified": unmodified,
        "time_to_approve_days": None if ttd is None else Decimal(ttd),
    }


def test_approver_patterns_basic():
    rows = [
        _decision(approver_id="A", decision="approved", ttd="1.0"),
        _decision(approver_id="A", decision="approved", ttd="2.0"),
        _decision(approver_id="A", decision="approved", ttd="3.0"),
        _decision(approver_id="A", decision="approved", ttd="1.0"),
        _decision(approver_id="A", decision="approved", ttd="3.0"),
        _decision(approver_id="A", decision="rejected"),
    ]
    pats = compute_approver_patterns(rows, names={"A": "Dana Lee"})
    assert len(pats) == 1
    p = pats[0]
    assert p.approver_id == "A"
    assert p.approver_name == "Dana Lee"
    assert p.approved_count == 5
    assert p.rejected_count == 1
    assert p.approval_rate_pct == Decimal("83.3")  # 5/6
    assert p.median_time_to_approve_days == Decimal("2.0")
    assert p.avg_time_to_approve_days == Decimal("2.0")
    assert p.sample_size == 6


def test_vendor_consistency():
    rows = [
        _decision(vendor_id="V1", vendor_name="Acme", amount="100", unmodified=True)
        for _ in range(24)
    ]
    pats = compute_vendor_patterns(rows)
    assert pats[0].consistency_pct == Decimal("100.0")

    for r in rows[:3]:
        r["unmodified"] = False
    pats = compute_vendor_patterns(rows)
    assert pats[0].consistency_pct == Decimal("87.5")  # 21/24


def test_vendor_amount_stats():
    rows = [
        _decision(vendor_id="V1", vendor_name="Acme", amount=a) for a in ("3900", "4800", "5200")
    ]
    p = compute_vendor_patterns(rows)[0]
    assert p.avg_approved_amount == Decimal("4633.33")
    assert p.median_approved_amount == Decimal("4800.00")
    assert p.min_approved_amount == Decimal("3900.00")
    assert p.max_approved_amount == Decimal("5200.00")


def test_stdev_decimal():
    vals = [Decimal(x) for x in ("2", "4", "4", "4", "5", "5", "7", "9")]
    assert _stdev(vals).quantize(Decimal("0.01")) == Decimal("2.00")


def test_baseline_insufficient_history():
    rows = [{"amount": Decimal("100"), "approver_id": "A", "time_to_approve_days": None}] * 4
    assert compute_vendor_baseline(rows, min_history=5) is None
    inv = SimpleNamespace(id="i1", vendor_id="V1", vendor_name="Acme", amount=Decimal("999"))
    res = detect_invoice_anomaly(inv, None)
    assert res.insufficient_history is True
    assert res.flags == []
    assert res.baseline is None


def _baseline(mean, stdev, *, median=None, approvers=None, med_time="0"):
    return VendorBaseline(
        vendor_id="V1",
        vendor_name="Acme",
        sample_size=24,
        mean_amount=Decimal(mean),
        median_amount=Decimal(median if median is not None else mean),
        stdev_amount=Decimal(stdev),
        min_amount=Decimal("0"),
        max_amount=Decimal("0"),
        typical_approver_ids=approvers or [],
        median_time_to_approve_days=Decimal(med_time),
    )


def test_anomaly_amount_high_flagged_with_baseline():
    baseline = _baseline("4812.50", "410.20", median="4800.00")
    inv = SimpleNamespace(id="i1", vendor_id="V1", vendor_name="Acme", amount=Decimal("19500"))
    res = detect_invoice_anomaly(inv, baseline)
    codes = [f.code for f in res.flags]
    assert codes == ["amount_high"]
    # Explainability: baseline returned + expected bound is mean + 2σ.
    assert res.baseline is baseline
    expected = Decimal("4812.50") + Decimal("2.0") * Decimal("410.20")
    assert res.flags[0].expected == str(expected)


def test_anomaly_tight_variance_guard():
    # σ=0, all amounts 5000; invoice 5200: > mean+2σ but NOT > median*3 → no flag.
    baseline = _baseline("5000", "0", median="5000")
    inv = SimpleNamespace(id="i1", vendor_id="V1", vendor_name="Acme", amount=Decimal("5200"))
    res = detect_invoice_anomaly(inv, baseline)
    assert [f.code for f in res.flags] == []


def test_anomaly_unusual_approver():
    baseline = _baseline("5000", "100", median="5000", approvers=["A", "B"])
    inv = SimpleNamespace(id="i1", vendor_id="V1", vendor_name="Acme", amount=Decimal("5000"))
    res = detect_invoice_anomaly(inv, baseline, proposed_approver_id="Z")
    assert "unusual_approver" in [f.code for f in res.flags]
    res2 = detect_invoice_anomaly(inv, baseline, proposed_approver_id="A")
    assert "unusual_approver" not in [f.code for f in res2.flags]


def test_derive_auto_approve_suggestion():
    rows = [
        _decision(vendor_id="V1", vendor_name="Acme", amount=str(4000 + i * 50), unmodified=True)
        for i in range(24)
    ]
    pats = compute_vendor_patterns(rows)
    sugg = derive_suggestions(pats)
    assert len(sugg) == 1
    s = sugg[0]
    assert s.kind == "auto_approve_threshold"
    # max amount is 4000+23*50 = 5150 → round up to 5500.
    assert s.payload["suggested_threshold"] == "5500.00"
    assert s.confidence_pct <= Decimal("99")
    assert s.dedupe_key == "auto_approve_threshold:V1"

    # One rejection → no suggestion.
    rows_rej = rows + [_decision(vendor_id="V1", vendor_name="Acme", decision="rejected")]
    assert derive_suggestions(compute_vendor_patterns(rows_rej)) == []

    # 11 approvals (< min_history 12) → none.
    assert derive_suggestions(compute_vendor_patterns(rows[:11])) == []


def test_derive_no_suggestion_when_any_modification():
    # 20 approvals, 1 real modification → consistency 95.0 (clears the % floor)
    # but the gate is absolute: any modification disqualifies. This pins the
    # control-bypass the rationale ("approved with no corrections") must never
    # falsely claim — see backend/docs/adaptive-workflows.md.
    rows = [
        _decision(vendor_id="V1", vendor_name="Acme", amount="4800", unmodified=True)
        for _ in range(20)
    ]
    rows[0]["unmodified"] = False  # exactly one corrected approval
    pats = compute_vendor_patterns(rows)
    assert pats[0].consistency_pct == Decimal("95.0")  # at the old % boundary
    assert pats[0].unmodified_count == 19
    assert pats[0].approved_count == 20
    # Absolute gate → no suggestion despite clearing the 95% floor.
    assert derive_suggestions(pats) == []

    # Even one modification in a large, otherwise-spotless history disqualifies.
    big = [
        _decision(vendor_id="V2", vendor_name="Beta", amount="4800", unmodified=True)
        for _ in range(100)
    ]
    big[0]["unmodified"] = False  # 97.0% consistency, still disqualified
    assert derive_suggestions(compute_vendor_patterns(big)) == []

    # And the truthful happy path: title reflects the real unmodified numerator.
    clean = [
        _decision(vendor_id="V3", vendor_name="Gamma", amount="4800", unmodified=True)
        for _ in range(20)
    ]
    s = derive_suggestions(compute_vendor_patterns(clean))[0]
    assert "20/20 invoices approved unmodified" in s.title


# ---------------------------------------------------------------------------
# Smart routing — pure-function tests
# ---------------------------------------------------------------------------


def _pat(approver_id, *, approved, rejected=0, median_ttd="1.0"):
    """Build an ApproverPattern with the fields recommend_approvers reads."""
    total = approved + rejected
    rate = Decimal("0") if total == 0 else (Decimal(approved) / Decimal(total) * 100)
    return ApproverPattern(
        approver_id=approver_id,
        approver_name=None,
        approved_count=approved,
        rejected_count=rejected,
        approval_rate_pct=rate.quantize(Decimal("0.1")),
        median_time_to_approve_days=Decimal(median_ttd),
        avg_time_to_approve_days=Decimal(median_ttd),
        sample_size=total,
    )


def test_routing_prefers_faster_consistent_approver():
    # A is fast + clean; B is slow + has rejections. A should rank first.
    pats = [
        _pat("A", approved=20, rejected=0, median_ttd="1.0"),
        _pat("B", approved=10, rejected=10, median_ttd="10.0"),
    ]
    eligible = [
        EligibleApprover("A", "Ann"),
        EligibleApprover("B", "Bob"),
    ]
    res = recommend_approvers(eligible, pats, vendor_id="V1", vendor_name="Acme")
    assert res.insufficient_history is False
    assert [c.approver_id for c in res.candidates] == ["A", "B"]
    assert res.candidates[0].rank == 1
    assert res.candidates[1].rank == 2
    assert res.candidates[0].score > res.candidates[1].score
    # Advisory only — no assignment field, just a ranked read model.
    assert res.candidates[0].approver_name == "Ann"


def test_routing_vendor_familiarity_breaks_close_race():
    # Two identical approvers; the one familiar with the vendor wins.
    pats = [
        _pat("A", approved=15, rejected=0, median_ttd="2.0"),
        _pat("B", approved=15, rejected=0, median_ttd="2.0"),
    ]
    eligible = [
        EligibleApprover("A", "Ann", vendor_approved_count=0),
        EligibleApprover("B", "Bob", vendor_approved_count=5),
    ]
    res = recommend_approvers(eligible, pats, vendor_id="V1", vendor_name="Acme")
    assert res.candidates[0].approver_id == "B"
    assert res.candidates[0].vendor_approved_count == 5
    assert any("this vendor" in r for r in res.candidates[0].reasons)


def test_routing_new_approver_with_no_history_is_routable_but_last():
    # C has no decision history and no familiarity → appears, scored 0, ranked last,
    # and insufficient_history stays False because A/B do have history.
    pats = [_pat("A", approved=20, rejected=0, median_ttd="1.0")]
    eligible = [
        EligibleApprover("A", "Ann"),
        EligibleApprover("C", "Cal"),  # not in patterns at all
    ]
    res = recommend_approvers(eligible, pats, vendor_id="V1", vendor_name="Acme")
    assert res.insufficient_history is False
    ids = [c.approver_id for c in res.candidates]
    assert ids == ["A", "C"]
    cal = res.candidates[1]
    assert cal.sample_size == 0
    assert cal.score == Decimal("0.0")
    assert "no approval history yet" in cal.reasons


def test_routing_insufficient_history_when_nobody_has_acted():
    # No patterns, no familiarity → insufficient_history True (caller falls back).
    eligible = [EligibleApprover("A", "Ann"), EligibleApprover("B", "Bob")]
    res = recommend_approvers(eligible, [], vendor_id="V1", vendor_name="Acme")
    assert res.insufficient_history is True
    # Still returns the candidates (ranked, all score 0) so the UI can list them.
    assert {c.approver_id for c in res.candidates} == {"A", "B"}
    assert all(c.score == Decimal("0.0") for c in res.candidates)


def test_routing_top_n_caps_the_list():
    pats = [_pat(x, approved=10, rejected=0) for x in ("A", "B", "C", "D")]
    eligible = [EligibleApprover(x, x) for x in ("A", "B", "C", "D")]
    res = recommend_approvers(eligible, pats, top_n=2)
    assert len(res.candidates) == 2


def test_routing_is_advisory_no_mutation_surface():
    # Sanity: the suggestion object exposes no apply/assign affordance — it is a
    # frozen read model of ranked candidates only.
    res = recommend_approvers([EligibleApprover("A", "Ann")], [_pat("A", approved=5)])
    assert not hasattr(res, "assign")
    assert not hasattr(res.candidates[0], "apply")


# ---------------------------------------------------------------------------
# Real-DB / API tests (realdb fixture)
# ---------------------------------------------------------------------------


async def _seed_approved_vendor(
    mk,
    org_id,
    actor_id,
    *,
    vendor_name="Acme Cleaning",
    amounts=None,
    unmodified=True,
    number_prefix="INV",
):
    """Create N approved invoices for one vendor with matching approval audit
    rows + ready_for_review clock-start rows. Returns the vendor_id."""
    from app.models.invoice import Invoice, InvoiceStatus
    from app.models.vendor import Vendor
    from app.models.workflow import AuditLog

    amounts = amounts or [Decimal("4800")] * 24
    async with mk() as s:
        vendor = Vendor(organization_id=org_id, name=vendor_name, status="active")
        s.add(vendor)
        await s.commit()
        await s.refresh(vendor)
        base = datetime.now(UTC) - timedelta(days=30)
        for i, amt in enumerate(amounts):
            inv = Invoice(
                organization_id=org_id,
                invoice_number=f"{number_prefix}-{i}",
                vendor_name=vendor_name,
                vendor_id=vendor.id,
                amount=amt,
                status=InvoiceStatus.approved,
            )
            s.add(inv)
            await s.commit()
            await s.refresh(inv)
            ready_at = base + timedelta(days=i)
            approved_at = ready_at + timedelta(days=1)
            s.add(
                AuditLog(
                    organization_id=org_id,
                    actor_id=None,
                    action="invoice.status_changed",
                    entity_type="invoice",
                    entity_id=inv.id,
                    details={"new_status": "ready_for_review"},
                    created_at=ready_at,
                )
            )
            s.add(
                AuditLog(
                    organization_id=org_id,
                    actor_id=actor_id,
                    action="invoice.approved",
                    entity_type="invoice",
                    entity_id=inv.id,
                    details=None
                    if unmodified
                    else {"changes": {"amount": {"old": "1", "new": "2"}}},
                    created_at=approved_at,
                )
            )
        await s.commit()
        return vendor.id


async def _seed_in_review_invoice(mk, org_id, *, vendor_id, vendor_name, amount, number="INV-IR"):
    from app.models.invoice import Invoice, InvoiceStatus

    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            invoice_number=number,
            vendor_name=vendor_name,
            vendor_id=vendor_id,
            amount=Decimal(amount),
            status=InvoiceStatus.ready_for_review,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return inv.id


async def test_approval_patterns_endpoint(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["ap_manager"]
    await _seed_approved_vendor(mk, org_id, actor_id, amounts=[Decimal("4800")] * 24)

    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.get("/api/adaptive/approval-patterns")
    assert r.status_code == 200
    data = r.json()
    vrow = next(v for v in data["vendors"] if v["vendor_name"] == "Acme Cleaning")
    assert vrow["approved_count"] == 24
    assert vrow["consistency_pct"] == "100.0"
    # Amounts serialise as strings (string-Decimal convention).
    assert isinstance(vrow["avg_approved_amount"], str)


async def test_anomaly_endpoint_flags_out_of_range_with_baseline(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["ap_manager"]
    amounts = [Decimal(str(4800 + (i % 5 - 2) * 200)) for i in range(24)]
    vid = await _seed_approved_vendor(mk, org_id, actor_id, amounts=amounts)
    inv_id = await _seed_in_review_invoice(
        mk, org_id, vendor_id=vid, vendor_name="Acme Cleaning", amount="19500"
    )

    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.get(f"/api/adaptive/anomalies?invoice_id={inv_id}")
    assert r.status_code == 200
    data = r.json()
    assert "amount_high" in [f["code"] for f in data["flags"]]
    assert data["baseline"] is not None  # explainability contract
    assert data["baseline"]["sample_size"] == 24


async def test_anomaly_endpoint_insufficient_history(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["ap_manager"]
    vid = await _seed_approved_vendor(
        mk, org_id, actor_id, amounts=[Decimal("4800")] * 3, vendor_name="Thin Vendor"
    )
    inv_id = await _seed_in_review_invoice(
        mk, org_id, vendor_id=vid, vendor_name="Thin Vendor", amount="9999"
    )
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.get(f"/api/adaptive/anomalies?invoice_id={inv_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["insufficient_history"] is True
    assert data["flags"] == []
    assert data["baseline"] is None


async def test_suggestions_persist_and_dismiss(realdb):
    from sqlalchemy import select

    from app.models.adaptive_suggestion import WorkflowSuggestion

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["ap_manager"]
    await _seed_approved_vendor(mk, org_id, actor_id, amounts=[Decimal("4800")] * 24)

    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.get("/api/adaptive/suggestions")
        assert r.status_code == 200
        suggestions = r.json()["suggestions"]
        assert len(suggestions) == 1
        sid = suggestions[0]["id"]

        # Persisted row exists.
        async with mk() as s:
            row = (
                await s.execute(
                    select(WorkflowSuggestion).where(WorkflowSuggestion.id == uuid.UUID(sid))
                )
            ).scalar_one()
            assert row.status == "open"

        d = await client.post(f"/api/adaptive/suggestions/{sid}/dismiss")
        assert d.status_code == 200
        assert d.json()["suggestions"][0]["status"] == "dismissed"

        # Re-GET re-runs derivation but the dismissal is durable.
        r2 = await client.get("/api/adaptive/suggestions?status=all")
        statuses = {s_["id"]: s_["status"] for s_ in r2.json()["suggestions"]}
        assert statuses[sid] == "dismissed"


async def test_suggestion_goes_stale(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["ap_manager"]
    vid = await _seed_approved_vendor(mk, org_id, actor_id, amounts=[Decimal("4800")] * 24)

    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.get("/api/adaptive/suggestions")
        assert len(r.json()["suggestions"]) == 1

        # Add a rejection so the condition (0 rejections) no longer holds.
        from app.models.invoice import Invoice, InvoiceStatus
        from app.models.workflow import AuditLog

        async with mk() as s:
            inv = Invoice(
                organization_id=org_id,
                invoice_number="INV-REJ",
                vendor_name="Acme Cleaning",
                vendor_id=vid,
                amount=Decimal("100"),
                status=InvoiceStatus.rejected,
            )
            s.add(inv)
            await s.commit()
            await s.refresh(inv)
            s.add(
                AuditLog(
                    organization_id=org_id,
                    actor_id=actor_id,
                    action="invoice.rejected",
                    entity_type="invoice",
                    entity_id=inv.id,
                    details={"reason": "no"},
                    created_at=datetime.now(UTC),
                )
            )
            await s.commit()

        r2 = await client.get("/api/adaptive/suggestions?status=all")
        statuses = [s_["status"] for s_ in r2.json()["suggestions"]]
        assert "stale" in statuses


async def test_auth_required(realdb):
    async with realdb.client(key="a", role=None) as client:
        for path, method in (
            ("/api/adaptive/approval-patterns", "get"),
            ("/api/adaptive/anomalies", "get"),
            ("/api/adaptive/suggestions", "get"),
            (f"/api/adaptive/suggestions/{uuid.uuid4()}/dismiss", "post"),
        ):
            resp = await getattr(client, method)(path)
            assert resp.status_code == 401, path


async def test_rbac_dismiss_forbidden_for_clerk(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["ap_manager"]
    await _seed_approved_vendor(mk, org_id, actor_id, amounts=[Decimal("4800")] * 24)

    async with realdb.client(key="a", role="ap_manager") as client:
        sid = (await client.get("/api/adaptive/suggestions")).json()["suggestions"][0]["id"]

    # Clerk excluded from read routes (manager/CFO surface).
    async with realdb.client(key="a", role="ap_clerk") as clerk:
        assert (await clerk.get("/api/adaptive/approval-patterns")).status_code == 403
        assert (await clerk.post(f"/api/adaptive/suggestions/{sid}/dismiss")).status_code == 403

    async with realdb.client(key="a", role="admin") as admin:
        assert (await admin.post(f"/api/adaptive/suggestions/{sid}/dismiss")).status_code == 200


async def test_suggestions_scoped_to_organization(realdb):
    """A WorkflowSuggestion row belonging to a different organization_id in the
    same tenant DB must not be marked stale by org A's recompute, nor returned
    in org A's listing. Pins the org-scope on the stale-sweep + listing queries
    inside suggestions() (DB-per-tenant already blocks cross-tenant leakage; the
    organization_id scope guards a stray row within one physical tenant DB)."""
    from sqlalchemy import select

    from app.models.adaptive_suggestion import WorkflowSuggestion

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["ap_manager"]
    await _seed_approved_vendor(mk, org_id, actor_id, amounts=[Decimal("4800")] * 24)

    # Plant a foreign-org suggestion row directly in tenant A's DB.
    other_org = uuid.uuid4()
    foreign_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            WorkflowSuggestion(
                id=foreign_id,
                organization_id=other_org,
                kind="auto_approve_threshold",
                dedupe_key="auto_approve_threshold:FOREIGN",
                vendor_name="Foreign Vendor",
                title="Foreign org suggestion",
                rationale="should never surface to org A",
                payload={"vendor_id": None},
                confidence_pct=Decimal("90"),
                status="open",
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as client:
        # Recompute (write-on-GET) — must not touch the foreign row.
        listed = (await client.get("/api/adaptive/suggestions?status=all")).json()["suggestions"]
        assert all(s_["id"] != str(foreign_id) for s_ in listed)

    # The foreign row is untouched — still "open", not flipped to "stale".
    async with mk() as s:
        row = (
            await s.execute(select(WorkflowSuggestion).where(WorkflowSuggestion.id == foreign_id))
        ).scalar_one()
        assert row.status == "open"


async def test_routing_suggestion_endpoint(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["ap_manager"]
    # ap_manager approves 24 of this vendor's invoices → history + familiarity.
    vid = await _seed_approved_vendor(mk, org_id, actor_id, amounts=[Decimal("4800")] * 24)
    inv_id = await _seed_in_review_invoice(
        mk, org_id, vendor_id=vid, vendor_name="Acme Cleaning", amount="5000"
    )

    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.get(f"/api/adaptive/routing-suggestion?invoice_id={inv_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["insufficient_history"] is False
    assert data["invoice_id"] == str(inv_id)
    # The approving manager appears, with vendor familiarity, ranked first.
    top = data["candidates"][0]
    assert top["approver_id"] == str(actor_id)
    assert top["rank"] == 1
    assert top["vendor_approved_count"] == 24
    # Scores serialise as string-Decimal (no wire floats).
    assert isinstance(top["score"], str)
    # ap_clerk is NOT an eligible approver → never a candidate.
    clerk_id = str(realdb.info("a").users["ap_clerk"])
    assert all(c["approver_id"] != clerk_id for c in data["candidates"])


async def test_routing_suggestion_404_outside_tenant(realdb):
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.get(f"/api/adaptive/routing-suggestion?invoice_id={uuid.uuid4()}")
    assert r.status_code == 404


async def test_routing_suggestion_rbac_and_auth(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["ap_manager"]
    vid = await _seed_approved_vendor(mk, org_id, actor_id, amounts=[Decimal("4800")] * 24)
    inv_id = await _seed_in_review_invoice(
        mk, org_id, vendor_id=vid, vendor_name="Acme Cleaning", amount="5000"
    )
    path = f"/api/adaptive/routing-suggestion?invoice_id={inv_id}"

    # Unauthenticated → 401.
    async with realdb.client(key="a", role=None) as anon:
        assert (await anon.get(path)).status_code == 401
    # Clerk excluded from this manager/CFO read surface → 403.
    async with realdb.client(key="a", role="ap_clerk") as clerk:
        assert (await clerk.get(path)).status_code == 403
    # CFO allowed.
    async with realdb.client(key="a", role="cfo") as cfo:
        assert (await cfo.get(path)).status_code == 200


async def test_tenant_isolation(realdb):
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    actor_a = realdb.info("a").users["ap_manager"]
    await _seed_approved_vendor(mk_a, org_a, actor_a, amounts=[Decimal("4800")] * 24)

    # Tenant B sees none of A's approvals.
    async with realdb.client(key="b", role="ap_manager") as client_b:
        data = (await client_b.get("/api/adaptive/approval-patterns")).json()
        assert all(v["vendor_name"] != "Acme Cleaning" for v in data["vendors"])
        suggestions = (await client_b.get("/api/adaptive/suggestions")).json()["suggestions"]
        assert suggestions == []
