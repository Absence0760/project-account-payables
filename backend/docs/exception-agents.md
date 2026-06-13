# Exception Agents — autonomous exception handling

AI agents that triage the exception queue: for each flagged invoice an agent
either auto-resolves it (mutating the invoice through the same audited path a
human would use) or escalates it to a human. Tenant-scoped, append-only
decision log, local-first (no LLM key required), opt-in per org.

**Status:** first slice. One fully-implemented resolver (small amount
mismatch); the rest are escalate-only stubs (see [Deferred](#deferred)).

## Data model — `AgentDecision`

`app/models/agent_decision.py`, table `agent_decisions` (tenant DB, migration
`0030_agent_decisions`). One **append-only** row per coordinator run.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `exception_id` | uuid FK → `exceptions.id` | indexed |
| `invoice_id` | uuid FK → `invoices.id` | indexed |
| `exception_type` | varchar(50) | copied from the exception |
| `action_taken` | varchar(20) | `auto_resolved` \| `escalated` \| `no_action` |
| `confidence` | numeric(5,4) | exact `Decimal` (never float), 0.0000–1.0000 |
| `rationale` | text | human-readable; LLM-polished when a key is set |
| `changes` | jsonb | `{"field": {"old": "<str>", "new": "<str>"}}`; money is string-Decimal. NULL when nothing changed |
| `autonomy_level` | varchar(20) | the level in force at decision time |
| `agent_type` | varchar(50) | which resolver decided (`amount_mismatch_v1`, a stub, or `none`) |
| `organization_id` | uuid | indexed |
| `entity_id` | uuid FK → `entities.id` | from `EntityMixin`; copied from the invoice |
| `created_at` / `updated_at` | timestamptz | from `TimestampMixin` |

Append-only is enforced by convention (no UPDATE/DELETE path in code). It is the
**decision log**, NOT the audit trail — the DB-immutable `audit_log` row for the
invoice mutation is written separately (see [Two audit rows](#two-audit-rows)).

## Architecture

Mirrors the project's adapter-registry layout (`erp_adapters/` et al.).

```
app/services/exception_agents/
├── base.py            # ExceptionResolver ABC + AgentEvaluation + action constants
├── registry.py        # @register_exception_agent decorator + get_resolver()
├── autonomy.py        # autonomy_level → confidence threshold
├── llm_rationale.py   # optional LLM rationale polish (fail-soft, offline default)
├── coordinator.py     # run_agent(): evaluate → (apply | escalate) → record AgentDecision
├── __init__.py        # re-exports + imports resolvers for decorator side effects
└── resolvers/
    ├── amount_mismatch.py  # the one real resolver (po_mismatch amount variance)
    └── stubs.py            # escalate-only stubs (missing_data, duplicate, fraud_flag)
```

- A **resolver** handles exactly one `exception_type`. `evaluate(...)` returns an
  `AgentEvaluation` (recommended action + confidence + proposed `changes`) and
  performs **no mutation**. `apply(...)` enacts the resolution and MUST write the
  invoice-mutation audit row(s); the default is a no-op (for escalate-only
  stubs).
- The **coordinator** (`run_agent`) is generic: resolve the org's autonomy
  threshold, dispatch to the resolver, decide whether confidence clears the
  threshold, call `apply` (or escalate), and always persist one `AgentDecision`.

### Coordinator flow

1. Resolve `autonomy_level` → confidence `threshold`.
2. `get_resolver(exception_type)`; if none → record `no_action` (status
   untouched), commit, return.
3. `resolver.evaluate(...)` → `AgentEvaluation`.
4. `can_resolve = recommended == auto_resolved AND confidence >= threshold`.
   - If yes: `resolver.apply(...)` (writes audit rows), mark the exception
     `resolved` (with `time_to_resolution_seconds`, `resolved_by = "AP Agent"`).
   - If no: set exception `status = escalated`.
   - If `apply` raises `NotApprovable` (invoice not in `ready_for_review`):
     downgrade to `escalated`, nothing was committed.
5. Persist one `AgentDecision`, commit.

## Autonomy → threshold

`Organization.settings.exception_agents.autonomy_level` controls **whether to
act**:

| Level | Confidence threshold | Effect |
|-------|----------------------|--------|
| `conservative` (default) | `1.01` | unreachable — **everything escalates** (safe, opt-in-required default) |
| `balanced` | `0.90` | auto-resolve when the resolver is ≥ 90% confident |
| `aggressive` | `0.75` | auto-resolve when ≥ 75% confident |

Unknown / absent levels fall back to `conservative` (fail-closed). `conservative`
being `1.01` makes "off" a data property, not a special-case branch — the
coordinator's single `confidence >= threshold` comparison naturally escalates
everything.

## The amount-mismatch resolver (`amount_mismatch_v1`)

Handles `exception_type == "po_mismatch"` where the **only** issue is an amount
variance within a tight tolerance: adjust the invoice amount to the PO total and
approve.

- **Data source:** the **live** `PurchaseOrder` row, re-matched via
  `match_invoice_to_po` inside `evaluate` (and again under the invoice row lock
  in `apply`). The resolver does **not** trust the `invoice.po_match` JSONB
  snapshot — that snapshot can be stale (PO re-synced/edited after it was
  written) and it doesn't distinguish a clean amount variance from a `partial`
  3-way receipt. Re-matching closes both gaps in one read.
- **In-scope only when the live match status is exactly `matched`.** `no_po`,
  `mismatch` (variance beyond the matcher's own band), and `partial` (3-way
  underdelivery — goods only partially received) all **escalate** — paying a
  partially-received PO in full would be wrong.
- **Currency:** a `PurchaseOrder` has no currency of its own in the schema; its
  `total` is denominated in the invoice's currency, so there is no
  cross-currency comparison to make. If per-PO currency is ever added, the
  resolver must gate on `invoice.currency == po.currency` before adjusting.
- **Tolerance:** default **2.5%** (deliberately tighter than the matcher's 5%
  warning band so the agent never silently absorbs a variance a human reviewer
  would still consider material). Override via
  `Organization.settings.exception_agents.amount_tolerance_pct`.
- **Decimal math (load-bearing):** `po_total` and the current amount are
  `Decimal(str(...))` then `.quantize(Decimal("0.01"))` — never `float`.
  `variance_pct = abs(current - po_total) / po_total * 100`, quantized to `0.01`.
- **Auto-fix only when** the live status is `matched`, `variance_pct <= tolerance`,
  `current != po_total`, and the reconciled amount does not trip a CFO/maximum
  approval gate. Confidence is `0.95`. `apply` re-runs the match under the row
  lock and bails (→ escalate) if the live PO total moved before it adjusts.
- The new amount is exactly `po_total`, passed as a `Decimal` into
  `review.approve_invoice(corrections={"amount": ...})` — the same audited
  correction path a human approver uses.

### CFO gate (not bypassed)

`approve_invoice` enforces the workflow snapshot's `require_cfo_above` /
`max_invoice_amount`. The agent approves as `ap_manager`, so rather than
bypassing the gate it reads the snapshot in `evaluate` and **escalates** when the
reconciled amount would require CFO sign-off (or exceeds the hard max). The
money-path invariant stays intact — the agent never grants itself a `cfo` role.

### Two audit rows

A successful auto-resolve writes **two** rows:

1. **`invoice.approved` in `audit_log`** — `approve_invoice` builds a
   `build_field_diff` (`{"amount": {"old": "...", "new": "..."}}`, string-Decimal)
   and `transition_invoice` writes the row with `details={"changes": ...}`. This
   is the DB-immutable mutation-audit row.
2. **`AgentDecision`** — the decision log (this feature's own product). It is
   **not** a substitute for the audit row.

## No-LLM-key fail-soft

`llm_rationale.build_rationale` mirrors `audit_summary.py`: the deterministic
`template` rationale is always the fallback, an LLM is consulted **only** when a
key is configured (reusing `AP_ANTHROPIC_API_KEY` + `AP_EXTRACTION_MODEL`, or the
org's BYOK extraction key), and there is **zero network call** in the no-key
local default. The *decision* (action + confidence + amount change) is 100%
rules-derived — the LLM only rewords the rationale string.

## API

All under `/api/exceptions`, all behind `require_roles(admin, ap_manager)`.
Registered **before** `exceptions.router` so the literal collection routes win
over `exceptions.py`'s `/{exception_id}/...` matcher.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/exceptions/{exception_id}/agent-resolve` | Run the agent on one `open`/`escalated` exception. Returns the new exception status + the `AgentDecision`. |
| `GET` | `/api/exceptions/agent-decisions` | Paginated decision log; filter by `exception_type`, `action_taken`. |
| `GET` | `/api/exceptions/agent-stats` | Aggregate counts + resolution/escalation rate. `accuracy` is a placeholder (needs a human-overturn signal). |

Response shapes are typed Pydantic `response_model`s in
`app/schemas/exception_agent.py` (`AgentDecisionResponse`,
`AgentDecisionListResponse`, `AgentStatsResponse`, `AgentResolveResponse`).

`confidence` is serialised as `float` in responses (display only) — stored exact.

**Concurrency:** `coordinator.run_agent` takes a `FOR UPDATE` lock on the
exception row and re-asserts its status before doing anything. The API's
pre-call status check is a TOCTOU on its own; two concurrent `agent-resolve`
calls would both pass it. The row lock serializes them — the loser re-reads the
now-`resolved` row and the coordinator raises `ExceptionNotActionable`, which
the API maps to **409** (no second `AgentDecision` row, no status clobber).

## Org settings

```json
{
  "exception_agents": {
    "autonomy_level": "conservative",
    "amount_tolerance_pct": 2.5
  }
}
```

Default (key absent) → `conservative` → everything escalates. No new env var:
the optional rationale LLM reuses `AP_ANTHROPIC_API_KEY` + `AP_EXTRACTION_MODEL`.

## Deferred

Registered as escalate-only stubs (so the coordinator dispatches by type) or
intentionally unregistered (so the coordinator records `no_action`). Each has a
roadmap follow-up.

| Exception type | State | Follow-up |
|----------------|-------|-----------|
| `missing_data` | stub — always escalates | missing-PO matcher / missing-data auto-fill |
| `duplicate` | stub — always escalates | duplicate auto-merge |
| `fraud_flag` | stub — always escalates | (likely stays human-gated) |
| `unverified_vendor`, `review_rejected`, `amount_exceeded`, `extraction_failed` | unregistered → `no_action` | human gates / out of scope for this slice |

Also deferred: GL-coding auto-resolve, the agent dashboard UI, a mobile surface,
adaptive/learned thresholds, and an accuracy metric (needs a human-overturn
signal — `agent-stats.accuracy` is `null` until then).
