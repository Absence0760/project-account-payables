---
description: Targeted audit of every money-moving path — invariants #1 (Decimal/Numeric), #2 (idempotency on writes that move money), and #3 (append-only audit trail). Use after changes to `app/api/payments.py`, `app/services/payment_*.py`, `app/services/card_*.py`, or any new schema/model that carries currency.
---

Sweep every code path that initiates, reverses, or confirms a payment and check that money stays Decimal end-to-end, writes are idempotent, and every state change writes an audit row.

## Why this command exists

The accounts-payable system's blast radius is dominated by money-movement bugs: paying the wrong vendor, paying twice, mis-rounding cents, or losing the SOC 2 audit trail behind a status change. Three classes of regression show up over and over:

1. **Money type drift.** A new column declared as `Float`. A service helper that does `float(amount)` mid-pipeline. A Pydantic field annotated `float`. Every one of these accumulates binary fractional error that disagrees with the DB on the last cent.
2. **Missing idempotency.** A handler that initiates a payment without checking a correlation_id / provider_payment_id. A webhook that creates a rebate without dedup. Two clicks → two payments.
3. **Silent state changes.** `invoice.status = X` directly in a handler, bypassing `transition_invoice`. The state changes; the audit row doesn't.

This command pins all three in one sweep.

## Procedure

### 1. Enumerate the money-moving surface

The endpoints / services that touch money state, as of today:

- `POST /api/payments/runs` → create draft run
- `POST /api/payments/runs/{id}/approve` → CFO sign-off
- `POST /api/payments/runs/{id}/cancel` → release the invoices back to queue
- `POST /api/payments/runs/{id}/execute` → dispatch to adapter
- `POST /api/payments/{id}/void` → reverse a settled payment
- `POST /api/payments/webhook/{tenant_slug}/{provider}` → settles / fails payments
- `POST /api/cards/generate` → mint a virtual card
- `POST /api/cards/{id}/cancel` → cancel a card
- `POST /api/cards/webhook/{provider}` → auth / settle a card → mint rebate
- Services: `services/payment_erp_sync.py`, `services/card_issuance.py`, `services/payment_reconciler.py`

If `grep -rEn '@router\.(post|put|patch).*(payment|card|rebate|run|payable)'` finds something NOT in this list, that endpoint is missing from the audit surface — automatic finding.

### 2. For each path, run the three invariants

For each money-moving endpoint or service:

#### Invariant #1 — Money is exact

- Every currency column on the model is `Numeric(p, s)`, not `Float` / `Real`. (`test_money_invariants.py` already pins this — re-run if anything changed.)
- Every `:amount` / `:total` / `:price` field on a Pydantic schema declares `Decimal` (or a Decimal-typed alias).
- Every service helper that returns a money value returns `Decimal`, not `float`. Grep for `float(` in money-named scopes — if any survive, that's a finding.

#### Invariant #2 — Idempotency on writes that move money

- Every state-changing handler has a precondition guard (e.g., "execute_payment_run refuses non-draft", "void_payment refuses already-voided/failed"). A handler that doesn't guard against the same request landing twice → **Critical**.
- Every webhook that mutates money state dedupes by event id (see `/audit-webhooks`).
- Every payment write that creates a row stamps a `correlation_id` so a downstream consumer can match it to its caller. A new path that omits `correlation_id` → **Improvement**.

#### Invariant #3 — Audit trail is append-only

- Every money-moving handler calls `dispatch_audit(...)` BEFORE `db.commit()`. A regression where the audit is after the commit means a crash between them leaves the DB out of sync with the audit log.
- The audit details include both old and new state for status changes. `transition_invoice` does this; a handler that assigns the status directly does not.
- No router exposes `PATCH` / `PUT` / `DELETE` on the audit-log endpoint (`test_audit_append_only.py` pins this).

### 3. Spawn the auditor for nuance

Once the structural checks above are done, call the security auditor for the parts that grep can't see — race conditions between commit and audit, currency rounding inside arithmetic helpers, schema fields that LOOK like money but aren't:

```
Agent({
  subagent_type: "repo-security-auditor",
  prompt: "Audit money-path integrity across the endpoints listed in
  `.claude/commands/audit-money-path.md`. For each, check (a) the
  amount stays Decimal end-to-end, (b) the handler is idempotent
  on retry, (c) every status mutation writes an audit row before
  commit. Cite file:line for every finding."
})
```

### 4. Render the report

Per-endpoint table:

```
## /audit-money-path report

### POST /api/payments/runs/{id}/execute
  - Money type:     PASS — Decimal throughout, adapter payload is Decimal
  - Idempotency:    PASS — refuses non-draft with 409
  - Audit trail:    PASS — transition_invoice + dispatch_audit on every payment

### POST /api/cards/webhook/{provider}
  ...

Summary: <N>/<total> paths fully compliant. Critical: <N>. Improvement: <N>.
```

## How "future bugs" get caught

The audit is invariant-driven, not pattern-driven. When a sixth payment path ships, the agent automatically asks the same three questions. New bug classes show up:

- A handler that calls `dispatch_audit` AFTER commit → fails invariant #3.
- A new schema that carries `: float` for a money field → fails invariant #1.
- A handler that doesn't check `payment.status` before mutating → fails invariant #2.
- An adapter helper that returns `0.0` (float) on the no-result path → fails invariant #1 once a caller adds it to a Decimal sum.

The structure stays the same as the codebase grows. Update the enumeration in step 1 when new endpoints land; the three-question framing keeps doing its job.
