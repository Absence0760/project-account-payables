# Exception lifecycle — the queue's audit trail

How an AP `Exception` is opened, routed, and closed, and where the record of
that lives. The autonomous side of the same queue is
[exception-agents.md](exception-agents.md); this doc covers the shared spine
both humans and agents run on.

## Why an exception is a control, not a note

Three exception types block a payment run outright —
`api/payments.PAYMENT_BLOCKING_EXCEPTION_TYPES`:

| Type | Raised by |
|---|---|
| `duplicate` | `services/invoice_warnings` duplicate detection |
| `fraud_flag` | fraud rules, Positive Pay returns |
| `line_total_mismatch` | line-total reconciliation (see [line-total-reconciliation.md](line-total-reconciliation.md)) |

Invoice **approval gates on none of them**. So clearing one of these is the
human sign-off that lets the money move — the last control between a flagged
payable and a funded payment run.

## Why the trail is in `audit_log`, not on the exception row

The `exceptions` row cannot be that record:

- **It is mutable and single-valued.** `status` / `resolution` / `resolved_by` /
  `resolved_at` hold only the LAST decision. An escalate-then-resolve loses the
  first decider entirely.
- **It is not shipped to the SOC 2 WORM store.** `services/audit_log_shipper`
  ships `audit_log` rows to CloudWatch + S3 Object Lock; nothing ships
  `exceptions`.
- **It carries no append-only trigger.** Migration `0022_sox_audit_immutable`
  installs DB-level `BEFORE UPDATE/DELETE` triggers on `audit_log` only.

So every lifecycle event writes an `audit_log` row through
`services/exception_lifecycle`.

## The events

| Action | Written when | Actor |
|---|---|---|
| `exception.raised` | `services/exception_service.create_exception` opens a row | usually `NULL` — a detector, not a person |
| `exception.resolved` | a human or an agent resolves it | the deciding user |
| `exception.escalated` | a human or an agent escalates it | the deciding user |
| `exception.dismissed` | a human dismisses it | the deciding user |
| `exception.assigned` | the queue routes it to (or away from) a user | the routing user |

`entity_type` is `"exception"`, `entity_id` the exception id.

**Correlation is the invoice's.** The row files under
`Invoice.correlation_id`, which is what `GET /api/audit/invoice/{id}`, the
auditor export (`GET /api/audit/export?invoice_id=`), and the invoice modal's
Activity timeline all select on — so an exception decision sits in the invoice's
own SOX trail beside `invoice.approved` / `invoice.rejected`. An invoice-less
exception (a Positive Pay `not_on_file` cheque the bank cleared that we never
issued) has no invoice to correlate to, so it uses its own id, which still
groups that exception's raise / assign / resolve rows together.

## What `details` carries

```json
{
  "exception_id": "…",
  "exception_type": "duplicate",
  "severity": "error",
  "invoice_id": "…",
  "payment_blocking": true,
  "old_status": "open",
  "new_status": "resolved",
  "resolution": "Confirmed distinct PO; not a duplicate.",
  "time_to_resolution_seconds": 5400,
  "via": "agent"
}
```

- **`payment_blocking`** is derived from
  `api/payments.PAYMENT_BLOCKING_EXCEPTION_TYPES` at write time
  (`exception_lifecycle.is_payment_blocking`), never a second hardcoded list —
  add a type to the gate and the trail follows. It is what tells an auditor
  this decision unblocked money.
- **`resolution`** is the decider's justification, truncated to 500 chars —
  same reason `invoice.rejected` carries its `reason`: it is the rationale, and
  the mutable row it also lives on can be overwritten.
- **`via: "agent"`** marks a non-interactive decision. `actor_id` still names
  the human who triggered the agent run — the agent has no identity of its own
  to hold accountable.
- The exception **`description` is deliberately not copied**. It is generated
  text that can name a vendor, the row already holds it, and the trail gains
  nothing by duplicating it.

## One chokepoint, three callers

`exception_lifecycle.record_decision` both applies the bookkeeping and writes
the row. Every decider goes through it:

- `api/exceptions.py` — `POST /{id}/resolve` and `POST /bulk/resolve`
- `services/exception_agents/coordinator.py` — auto-resolve and every escalate
  path
- `api/payments.py::_resolve_compliance_hold_exception` — the sanctions/KYC
  `payment_compliance_hold` cleared by `POST /payments/{id}/compliance/release`
  or `/dismiss`. It keeps the `resolve` verb in both cases: a dismissed
  *payment* still means a human cleared the hold, and `resolution`
  (`released` vs `dismissed: <reason>`) is what distinguishes them.

Previously these were three copies (the coordinator's helper carried a comment
saying it was mirroring the API's; the payments one said the same), and none
wrote an audit row.
`correlation_ids_for` resolves a whole batch's correlations in one query so a
bulk action doesn't fire one lookup per row.

## Escalation is not a resolution

`escalate` records the decision **note** — so the human picking it up reads why
it was raised, in the queue itself — but leaves `resolved_by`, `resolved_at`
and `time_to_resolution_seconds` alone. A still-open row advertising a resolver
and a resolution timestamp for work nobody has done misleads an auditor, and
the SLA clock is genuinely still running. Who escalated and when is on the
immutable `exception.escalated` row instead.

`time_to_resolution_seconds` is therefore stamped once, on the trip to a
genuinely terminal state (`resolve` / `dismiss`).

## Scoping and RBAC

Every `/api/exceptions` route is `require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)`
and **entity-scoped** — reads and mutations alike. An exception belonging to
another subsidiary is the same opaque 404 the detail read gives; a bulk call
folds an out-of-scope id into its existing `not_found` skip, so it can't
enumerate either. This matters for the same reason the audit row does: a
cross-entity clear of a `duplicate` releases money the caller can't see in
their own queue.

## The type roster

`exception_type` is a plain `String(50)` — there is no DB enum — so
`exception_lifecycle.EXCEPTION_TYPES` is the canonical roster instead, sitting
beside the rest of an exception's behaviour (payment-blocking, actionable,
auditable). Two things key off it:

- `api/exceptions.EXCEPTION_TYPE_LABELS` must cover it **exactly**. A missing
  entry isn't a crash — the lookup falls back to the raw key — so the queue just
  renders `line_total_mismatch` at an AP manager instead of "Line Total
  Mismatch". That matters most for exactly the types it kept happening to:
  payment-blocking ones, read under time pressure.
- `LEGACY_EXCEPTION_TYPES` names roster members nothing raises any more. They
  keep their seat and their label because historical rows still carry them;
  declaring them explicitly is what lets the guard still fail on a *new* dead
  entry.

`tests/test_exception_type_labels.py` enforces both by **AST-scanning `app/`**
for the type strings the code actually uses (the `exception_type=` keyword, the
positional argument of `_ensure_exception`, and any `*_EXCEPTION_TYPE(S)`
constant) rather than carrying a hand-maintained list. The list *was* the drift:
the previous version of that guard passed for `line_total_mismatch`,
`payment_compliance_hold` and `price_variance` while all three rendered raw.

## Where it surfaces

- `GET /api/audit/invoice/{id}` and the auditor export — the events, in order,
  in the invoice's trail.
- The invoice modal's Activity timeline (`InvoiceModal.svelte`) — labelled per
  action, translated in every locale.
- The deterministic audit summary (`services/audit_summary`) — names the
  exception type and the decider: *"flagged (duplicate), then had its exception
  cleared (duplicate) by Dana Clerk"*.

## Tests

`backend/tests/test_exception_audit_trail.py` — raise / resolve / escalate /
dismiss / assign, the invoice-less correlation fallback, the
`payment_blocking` drift guard against the real payment-run gate, agent parity
(`via: agent`), the resolution-length cap, and entity scoping on every mutating
endpoint. `backend/tests/test_exception_assignment.py` pins
`apply_resolution`'s pure bookkeeping.
`backend/tests/test_exception_type_labels.py` is the roster/label drift guard
described above (pure — an AST walk over `app/`, no DB).
