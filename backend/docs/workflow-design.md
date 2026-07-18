# Invoice Processing Workflow

This document describes the end-to-end workflow for processing an invoice from upload through ERP submission.

## Overview

```
┌────────┐     ┌─────────┐     ┌──────────┐     ┌───────────┐     ┌──────┐
│ Upload │ ──> │ Extract │ ──> │  Review  │ ──> │  Send to  │ ──> │ Done │
│        │     │  (AI)   │     │ (Human)  │     │    ERP    │     │      │
└────────┘     └─────────┘     └──────────┘     └───────────┘     └──────┘
```

Each invoice carries a **correlation ID** (UUID) that is propagated to every workflow instance, workflow step, audit log entry, and payment record — enabling full lifecycle traceability with a single query.

## Invoice Statuses

| Status              | Meaning                                                            | Workflow Stage     |
|---------------------|--------------------------------------------------------------------|--------------------|
| `new`               | Created, no file attached yet                                      | Pre-workflow       |
| `pending`           | File uploaded, AI extraction in progress                           | Stage 1: Extract   |
| `ready_for_review`  | Extraction complete, awaiting reviewer                             | Stage 2: Review    |
| `approved`          | Reviewer approved, ready for ERP push                              | Between 2 and 3    |
| `rejected`          | Reviewer rejected, needs edits/re-upload                           | Stage 2 (branch)   |
| `sending_to_erp`    | Async ERP submission in flight                                     | Stage 3: ERP Send  |
| `sent_to_erp`       | ERP confirmed receipt                                              | Stage 4 (start)    |
| `posted_in_erp`     | ERP posted/booked the invoice (set via inbound ERP webhook)        | Stage 4            |
| `payment_scheduled` | Added to a payment run, awaiting execution                         | Stage 5 (payments) |
| `paid`              | Payment executed                                                   | Stage 5            |
| `done`              | Workflow complete — terminal state, invoice is now immutable       | Final              |
| `failed`            | Any stage failed                                                   | Error state        |

The state machine in `services/workflow_engine.py` enforces every transition below — payment-path states (`posted_in_erp`, `payment_scheduled`, `paid`) are driven by inbound ERP webhooks + payment-run execution, but they go through `transition_invoice()` like any other transition, so they all produce audit-log rows.

## Status Transitions

```
new ──────────────> pending                  (file uploaded, extraction triggered)
new ──────────────> ready_for_review         (extraction skipped or pre-filled)
new ──────────────> approved                 (auto-approve confidence path)
new ──────────────> done                     (no workflow steps enabled → terminal)
pending ──────────> ready_for_review         (extraction succeeded)
pending ──────────> approved                 (auto-approve over threshold)
pending ──────────> failed                   (extraction failed / reaper)
ready_for_review ─> approved                 (reviewer approves)
ready_for_review ─> rejected                 (reviewer rejects)
rejected ─────────> ready_for_review         (re-submitted after edits)
rejected ─────────> new                      (requires full re-upload)
approved ─────────> sending_to_erp           (ERP submission initiated)
approved ─────────> payment_scheduled        (direct schedule — no ERP step)
approved ─────────> done                     (no further steps → terminal)
sending_to_erp ───> sent_to_erp              (ERP confirmed)
sending_to_erp ───> failed                   (ERP rejected or timed out)
sent_to_erp ──────> posted_in_erp            (ERP webhook confirms post)
sent_to_erp ──────> done                     (terminal — workflow complete)
posted_in_erp ────> payment_scheduled        (payment run picks up the invoice)
posted_in_erp ────> done                     (no payment step in workflow)
payment_scheduled > paid                     (processor settles the payment)
payment_scheduled > approved                 (void payment → back to queue)
paid ─────────────> done                     (terminal)
paid ─────────────> approved                 (void payment → back to queue)
failed ───────────> pending                  (retry extraction)
failed ───────────> sending_to_erp           (retry ERP push, if previously approved)
```

`done` is terminal. The `payment_scheduled → approved` and `paid → approved` back-edges are the **void-payment** path (`POST /api/payments/{id}/void`): the invoice returns to the queue so it can be re-scheduled. Every transition above writes an audit-log row through `transition_invoice()`; nothing in the payment path mutates `invoice.status` directly.

All transitions are enforced by a state machine in `services/workflow_engine.py`. Invalid transitions return `409 Conflict`. Authoritative graph: `VALID_TRANSITIONS` in that file.

## Stage 1: Upload & Extraction

**Endpoint:** `POST /api/invoices/upload` (multipart/form-data)

1. Accept file upload (PDF, PNG, JPEG, TIFF — max 25 MB).
2. Store file in S3/MinIO under `{organization_id}/{invoice_id}/{filename}`.
3. Create the Invoice record with `status=new` and placeholder fields (`invoice_number="PENDING"`, `amount=0`). Populate `file_key` and `file_url`.
4. Create a WorkflowInstance and the first WorkflowStep (`type=extraction`).
5. Transition invoice to `pending`.
6. Dispatch async AI extraction task.
7. Return `202 Accepted` with the invoice ID and `correlation_id`.

**Extraction outcomes:**
- **Success:** Extracted fields are written to the invoice, vendor matching links the invoice to a `Vendor`, the per-vendor correction cache overlays cached priors, RAG few-shots inform the prompt, and `services.invoice_warnings.refresh_warnings` runs to populate warnings, exceptions, and the **2/3-way PO match** (persisted on `invoice.po_match`). An `InvoiceExtractionResult` row is created with the confidence score and raw output, and the invoice transitions to `ready_for_review`.
- **Failure:** Invoice transitions to `failed`. Error details are stored in `WorkflowInstance.state_data`.
- **Timeout:** If extraction has not completed within 5 minutes, the invoice is transitioned to `failed` with reason `extraction_timeout`.

## Stage 2: Review

A human reviewer examines the extracted data and either approves or rejects the invoice.

### Approver Configuration

The approval step in the workflow definition supports three strategies:

| Strategy | Behavior |
|---|---|
| **Manual** | When submitting for review, the user picks an approver from a dropdown. The selected user is assigned and sees Approve/Reject buttons on the invoice. |
| **Specific** | One or more approvers are pre-configured in the workflow. Invoices are auto-assigned (round-robin). Only the assigned user sees the review buttons. |
| **Auto** | Invoices skip human review entirely and are auto-approved. |
| **Chain** | Multi-level sequential approval. Each level defines a min/max amount range, approver list, and required approval count. All approvals at level N must complete before level N+1 begins. See **Multi-Level Approval Chains** below. |

Multiple approvers can be configured for the "specific" strategy via the workflow editor's search-and-pick interface.

### Who Can Review

- If an invoice has an `assigned_to_id`, only that user sees Approve/Reject buttons in the modal.
- If no one is assigned, any user with a non-clerk role can review.
- AP Clerks never see review buttons regardless of assignment.

### Approval Thresholds

All thresholds are read from `steps_config_snapshot` (frozen per invoice at creation time).

| Config key | Behavior |
|---|---|
| `auto_approve_below` | Invoices with amount below this value skip human review entirely — auto-approved. |
| `require_cfo_above` | Non-CFO users receive 403 when attempting to approve invoices above this amount. |
| `max_invoice_amount` | Invoices above this amount are rejected outright (422). |

### Multi-Level Approval Chains

Strategy `"chain"` with `approval_chain: list[ApprovalLevelConfig]`.

Each `ApprovalLevelConfig`:

| Field | Purpose |
|---|---|
| `min_amount` | Lower bound for this level to apply |
| `max_amount` | Upper bound (nullable for open-ended) |
| `approver_ids` | List of eligible approver UUIDs |
| `required_approvals` | Number of approvals needed at this level |
| `name` | Display name (e.g. "Manager", "CFO") |

Chain state is tracked in `WorkflowInstance.state_data["approval_levels"]`. Levels are sequential: all approvals at level N must complete before level N+1 becomes active. The invoice stays in `ready_for_review` until all applicable levels are satisfied.

**Named-approver enforcement**: a non-empty `approver_ids` on the current level is a hard allow-list, enforced by `approval_chain.check_level_approver` before the approval is recorded — the endpoint's role-based RBAC gate (`require_permission(PERM_INVOICE_APPROVE)`, held by any `ap_manager`/`cfo`/`admin`) only confirms the actor holds an approving role, not that they are one of the named approvers, so this is a separate, additional check. An empty `approver_ids` list is unrestricted (any actor who cleared RBAC may approve, matching legacy behaviour). A named approver's active delegate (`User.delegate_to_id` / `delegate_until`) is also authorized. A non-authorized actor gets a 403 and the approval is not recorded.

The single-level strategy `"specific"` applies the same named-approver check (`approver_ids`, or the deprecated single `approver_id`) without the multi-level chain machinery — useful when a step needs exactly one or a small fixed set of eligible people but no sequential levels.

### Segregation of Duties

`require_segregation: bool` on the approval step config. When enabled:

- `Invoice.uploaded_by_id` tracks the user who uploaded the invoice.
- If the approver is the same user who uploaded (`uploaded_by_id == current_user.id`), the approval is rejected with 403.
- Skipped when `uploaded_by_id` is NULL (pre-existing invoices created before the field was added).

### Delegation / Out-of-Office

Users can designate a delegate who receives their approval assignments while they are away.

- `User.delegate_to_id` — FK to the delegate user (control plane).
- `User.delegate_until` — datetime after which delegation expires.
- `assign_reviewer()` checks delegation and reassigns to the delegate when active.
- `WorkflowStep.original_assigned_to` records the intended user so the audit trail shows both the original assignee and the delegate.

API:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/auth/delegation` | Check current delegation status |
| `POST` | `/api/auth/delegation` | Set delegate: `{delegate_to_id, until}` |
| `DELETE` | `/api/auth/delegation` | Clear delegation |

### Endpoints

| Method | Path                              | Purpose                          | Status Guard        |
|--------|-----------------------------------|----------------------------------|---------------------|
| POST   | `/api/invoices/{id}/assign`       | Assign a reviewer                | `ready_for_review`  |
| POST   | `/api/invoices/{id}/approve`      | Approve (with optional edits)    | `ready_for_review`  |
| POST   | `/api/invoices/{id}/reject`       | Reject with a reason             | `ready_for_review`  |
| POST   | `/api/invoices/{id}/resubmit`     | Re-enter review after edits      | `rejected`          |

### Approve

1. Validate invoice status is `ready_for_review`.
2. Apply any field corrections included in the request body.
3. Set `approval_date` to today, `approved_by` to the reviewer's name.
4. Transition to `approved`.
5. Complete the review WorkflowStep with `action=approved`.
6. Write audit log (`invoice.approved`).

### Reject

1. Validate invoice status is `ready_for_review`.
2. Set `rejected_by` to the reviewer's name.
3. Transition to `rejected`.
4. Complete the review WorkflowStep with `action=rejected`.
5. Create an exception record with the rejection reason.
6. Store rejection reason in the audit log details.
7. Write audit log (`invoice.rejected`).

### Resubmit

After a rejection, the user can edit the invoice fields and resubmit for another round of review. This transitions `rejected → ready_for_review` and creates a new review WorkflowStep.

A rejection counter is tracked in `WorkflowInstance.state_data`. After a configurable number of rejections, the invoice may be auto-escalated or locked.

## Stage 3: Send to ERP

**Endpoint:** `POST /api/invoices/{id}/send-to-erp`

Can be triggered manually by the user or automatically after approval.

1. Validate invoice status is `approved`.
2. Transition to `sending_to_erp`.
3. Create a WorkflowStep (`type=erp_export`).
4. Dispatch async ERP call. The invoice's `correlation_id` is sent as an idempotency key to prevent duplicate records in the ERP system.

### Retry Logic

| Scenario             | Behavior                                                                 |
|----------------------|--------------------------------------------------------------------------|
| Transient failure    | Retry with exponential backoff, up to 3 attempts. Stay in `sending_to_erp`. |
| Permanent failure    | Transition to `failed`. Record error in WorkflowStep and audit log.     |
| Success              | Transition to `sent_to_erp`. Store ERP reference ID in `state_data`.    |

**Manual retry:** `POST /api/invoices/{id}/retry-erp` — only valid when status is `failed` and the invoice was previously approved (i.e., `approved_by` is set).

## Stage 4: Done

`done` is the terminal state. When the ERP confirms receipt:

1. Complete the `erp_export` WorkflowStep.
2. Create a final WorkflowStep (`type=done`, `action=completed`).
3. Transition `sent_to_erp → done`.
4. Set `WorkflowInstance.state = "completed"`.
5. Write audit log (`invoice.erp_confirmed`).

The invoice is **immutable** in the later half of the lifecycle. PATCH and DELETE reject requests when the invoice is in `sending_to_erp`, `sent_to_erp`, `posted_in_erp`, `payment_scheduled`, `paid`, or `done` (the `IMMUTABLE_STATUSES` set in `app/api/invoices.py`).

## Workflow Models

### WorkflowDefinition

Represents the workflow template. The `steps_config` JSONB column defines the step sequence:

```json
{
  "steps": [
    { "number": 1, "type": "extraction", "name": "Data Extraction" },
    { "number": 2, "type": "approval",   "name": "Manager Approval" },
    { "number": 3, "type": "erp_export", "name": "ERP Export" },
    { "number": 4, "type": "done",       "name": "Complete" }
  ]
}
```

Step types `extraction`, `approval`, `erp_export`, `done` are canonical. Legacy aliases `upload`, `review`, `erp_push` are still accepted by `_STEP_TYPE_ALIASES` for backwards compatibility but new configs should use the canonical names.

Seeded per tenant at organization creation. Configurable for custom approval chains.

#### Per-entity selection (multi-entity Phase 3)

`workflow_definitions` carries a nullable `entity_id` (`EntityMixin`): a definition either belongs to a specific subsidiary (`entity_id` set) or is **shared / org-wide** (`entity_id IS NULL`). When a new invoice is created, `workflow_engine.get_or_create_workflow_definition(db, organization_id, entity_id)` (called by `create_workflow_instance` with `invoice.entity_id`) resolves which definition governs it, in precedence order:

1. The invoice's own entity's active definition — prefer its `is_default`, then the oldest active (`created_at` tiebreak).
2. Otherwise a shared / org-wide active definition (`entity_id IS NULL`) — same default-then-oldest ordering.

If neither exists the org-wide default is auto-created with `entity_id = NULL` and `is_default = true`, so a single-entity tenant keeps getting exactly one org-wide definition — fully backward compatible. The resolved definition's `steps_config` is then snapshotted onto the `WorkflowInstance` as usual (in-flight invoices never see a later edit).

At most one `is_default = true` definition may exist per `(organization_id, entity_id)`, enforced by the partial unique index `uq_workflow_definitions_one_default` on `(organization_id, COALESCE(entity_id, '00000000-…-0000'::uuid)) WHERE is_default = true` (the COALESCE sentinel collapses the NULL/shared bucket to a single key, since SQL treats `NULL != NULL`). The index is declared on the model (so fresh `create_all` tenants get it) and installed on existing tenants by migration `0050_workflow_per_entity_default` (which first demotes any pre-existing duplicate defaults, keeping the earliest `created_at` per group). See `../../docs/multi-entity.md`.

## No-Code Builder step types

The visual **No-Code Workflow Builder** lets an admin compose a workflow out of
the four canonical pipeline steps above plus five NEW builder step types. They are
stored in the **same** `steps_config` JSONB (`{ "steps": [ <step>, ... ] }`) — no
enum migration is needed. Each step keeps the standard shape:

```json
{ "number": 1, "type": "<type>", "name": "...", "enabled": true, "config": { ... } }
```

The builder types **orchestrate and branch**; they do **not** alter the invoice
state machine (`VALID_TRANSITIONS` is unchanged). The engine recognises them via
`workflow_engine.KNOWN_STEP_TYPES` / `is_known_step_type()` so a definition that
contains them is accepted rather than rejected. All builder logic lives in
`services/workflow_builder.py` and is consumed by the simulation service and the
import/create validation path.

| Type | What it does |
|------|--------------|
| `condition` | Branch the path on invoice field rules — jump (`goto`) to another step or fall through. |
| `parallel` | Fan an approval out to multiple branches; join on all / any / N approvals. |
| `webhook` | Call an external URL. **Recorded-not-sent by default** (local-first). |
| `email` | Send a notification through the existing email adapter (`console` default). |
| `delay` | Wait for a duration / until an invoice date field. **Never sleeps** — records intent. |

### Config shapes

- **condition**
  ```json
  { "rules": [ {"field": "amount", "operator": "gt", "value": 1000} ],
    "match": "all" | "any",
    "on_true_goto": <int|null>, "on_false_goto": <int|null> }
  ```
  - `field` ∈ `amount`, `currency`, `vendor_id`, `gl_account`, `cost_center`, `department`
  - `operator` ∈ `gt`, `gte`, `lt`, `lte`, `eq`, `ne`, `in`, `not_in`, `starts_with`
  - `*_goto` is a target step **`number`** (null = fall through to the next step)
- **parallel**
  ```json
  { "branches": [ {"name": "Finance", "approver_ids": ["..."]} ],
    "join": "all" | "any", "min_approvals": <int|null> }
  ```
- **webhook**
  ```json
  { "url": "https://...", "method": "POST"|"GET"|"PUT",
    "headers": {"X-Key": "..."}, "body_template": "...|null",
    "timeout_seconds": 10 }
  ```
- **email**
  ```json
  { "to": "approver"|"vendor"|"custom", "to_addresses": ["a@b.com"],
    "subject": "...", "body_template": "..." }
  ```
- **delay**
  ```json
  { "duration_seconds": 3600, "until_field": "due_date|null" }
  ```

### Condition goto / branching semantics

`evaluate_condition(config, ctx)` evaluates every rule against the invoice
context, then combines them with `match`:

- `match: "all"` (default) — every rule must pass (a rule-less condition is
  vacuously **true**).
- `match: "any"` — at least one rule must pass (a rule-less condition is
  vacuously **false**).

`amount` rules compare on `Decimal` (so `"100"` equals `100.00`); every other
field compares as a string. Numeric operators (`gt`/`gte`/`lt`/`lte`) always
coerce both sides to `Decimal`. The result carries the resolved branch target:
`goto = on_true_goto` when matched, else `on_false_goto`. A `null` goto means
"fall through to the next step." `validate_builder_steps` rejects a `goto` that
points at a step number not present in the workflow.

### Parallel join semantics

`resolve_parallel(config)` turns the join rule into a concrete `required`
approval count:

- `join: "all"` → every branch must approve (`required == len(branches)`).
- `join: "any"` → one approval clears the join (`required == 1`).
- `min_approvals` (when set) overrides the join, clamped to `[1, len(branches)]`.

The returned dict — `{branches, join, min_approvals, required}` — is what the
simulation/runtime uses to decide whether the parallel gate is satisfied.

### Local-first executor behavior

`execute_custom_step(step, ctx, *, dry_run=False)` runs the `webhook` / `email` /
`delay` steps and returns `{"type", "status": "ok"|"skipped"|"error", "detail"}`.
**`dry_run=True` (simulation) has zero side effects.** Per rail 7 (local-first),
every executor runs on a dev laptop with no cloud and no network:

- **webhook** — defaults to a no-network **recorded** result; the actual HTTP
  send belongs to deployed orchestration. Even when `config.enabled` is set, the
  engine records intent rather than calling out from the dev/simulation path. A
  missing `url` returns `status: "error"`.
- **email** — sends via the existing email adapter (`console` by default).
  `to: "custom"` with no addresses returns `skipped`; `approver`/`vendor`
  recipients are resolved by the caller at runtime (the engine records the kind
  only, never an address — PII-free). Adapter failures degrade to `error`,
  never raise.
- **delay** — **never sleeps**; it records the intended wait (`duration_seconds`
  or `until_field`). A deployed scheduler consumes the intent; dev + simulation
  proceed immediately.

`condition` and `parallel` are **not** executed through `execute_custom_step` —
they branch the path, so they're resolved via `evaluate_condition` /
`resolve_parallel` instead. `build_invoice_context(invoice)` maps an Invoice ORM
object (or a plain dict / SimInvoice) to the evaluation context, keeping `amount`
as a `Decimal` (never float).

### WorkflowInstance

One per invoice. Tracks progress through the definition's steps.

| Field            | Purpose                                                    |
|------------------|------------------------------------------------------------|
| `correlation_id` | Copied from the invoice for cross-table tracing            |
| `invoice_id`     | FK to the invoice                                          |
| `current_step`   | Index into the steps_config array                          |
| `state`          | `active`, `paused`, `completed`, `failed`                  |
| `state_data`     | JSONB — retry counts, extraction confidence, ERP ref, etc. |

### WorkflowStep

One row per step attempted. Created when a step begins, updated on completion.

| Field          | Purpose                                             |
|----------------|-----------------------------------------------------|
| `correlation_id`| Same as the instance                               |
| `step_number`  | Matches the definition step number                  |
| `step_type`    | `extraction`, `approval`, `erp_export`, `done` (canonical) |
| `assigned_to`  | Reviewer user UUID (for review steps)               |
| `action`       | Outcome: `extracted`, `approved`, `rejected`, etc.  |
| `completed_at` | When the step finished                              |

## Correlation ID

Every record related to an invoice shares the same `correlation_id`:

```
Invoice.correlation_id  (source of truth — auto-generated on creation)
    ├── WorkflowInstance.correlation_id
    │       └── WorkflowStep.correlation_id
    ├── AuditLog.correlation_id
    ├── PaymentSchedule.correlation_id
    └── Payment.correlation_id
```

To retrieve the full lifecycle of any invoice:

```sql
SELECT * FROM audit_log WHERE correlation_id = '<id>' ORDER BY created_at;
```

An `X-Correlation-ID` HTTP header is read/generated by middleware and stored in a context variable for structured logging.

## Audit Logging

Every state transition writes an audit log entry via `services/audit.py`. The `GET /api/invoices/{id}/audit-log` endpoint resolves `actor_id` UUIDs to human-readable `actor_name` strings by looking up users in the control-plane database. The frontend displays these in a timeline view within the invoice modal.

Each entry records:

- `correlation_id` — links to the invoice
- `organization_id` — tenant scoping
- `actor_id` — user who performed the action
- `action` — standardized action name
- `entity_type` / `entity_id` — what was changed
- `details` — JSONB with context (old/new status, rejection reason, ERP response, etc.)

### Action Names

| Action                         | Trigger                         |
|--------------------------------|---------------------------------|
| `invoice.created`              | Invoice created (manual entry)  |
| `invoice.uploaded`             | File uploaded                   |
| `invoice.extraction_completed` | AI extraction succeeded         |
| `invoice.extraction_failed`    | AI extraction failed            |
| `invoice.assigned_for_review`  | Reviewer assigned               |
| `invoice.approved`             | Reviewer approved               |
| `invoice.rejected`             | Reviewer rejected               |
| `invoice.resubmitted`         | Re-entered review after edits   |
| `invoice.erp_submitted`       | ERP push initiated              |
| `invoice.erp_confirmed`       | ERP confirmed receipt           |
| `invoice.erp_failed`          | ERP push failed                 |
| `invoice.erp_retried`         | Manual ERP retry                |

## Error Handling

| Scenario                    | Handling                                                             |
|-----------------------------|----------------------------------------------------------------------|
| Concurrent transitions      | `SELECT ... FOR UPDATE` prevents race conditions                     |
| Extraction timeout          | `services/extraction_reaper.py` runs in-process every `AP_EXTRACTION_REAPER_INTERVAL_SECONDS` (60s default); transitions any invoice in `pending` for more than `AP_EXTRACTION_TIMEOUT_SECONDS` (600s default) to `failed` and appends an `extraction_timeout` warning. Reviewer can re-trigger or fall back to manual entry. Same logic available as `python scripts/reap_stuck_extractions.py` for one-shot use. |
| Orphaned `sending_to_erp`   | (pending) — same pattern, not yet implemented for the ERP push path  |
| Duplicate invoices          | Check `invoice_number` + `vendor_name` per org; return `409 Conflict`|
| Rejection loops             | Track count in `state_data`; auto-escalate after N rejections        |
| File validation             | Max 25 MB; allowed types: PDF, PNG, JPEG, TIFF                      |
| ERP idempotency             | `correlation_id` sent as idempotency key to the ERP                  |

## API Endpoints Summary

### Workflow Actions

| Method | Path                                  | Purpose                                    | Returns |
|--------|---------------------------------------|--------------------------------------------|---------|
| POST   | `/api/invoices/upload`                | Upload file, start workflow                | 202     |
| POST   | `/api/invoices/{id}/extract`          | Manually (re-)trigger extraction           | 202     |
| POST   | `/api/invoices/{id}/reset-extraction` | Reset stuck `pending` extraction to `new`  | 200     |
| POST   | `/api/invoices/{id}/assign`           | Assign reviewer                            | 200     |
| POST   | `/api/invoices/{id}/approve`          | Approve invoice                            | 200     |
| POST   | `/api/invoices/{id}/reject`           | Reject invoice                             | 200     |
| POST   | `/api/invoices/{id}/resubmit`         | Resubmit for review                        | 200     |
| POST   | `/api/invoices/{id}/send-to-erp`      | Initiate ERP push                          | 202     |
| POST   | `/api/invoices/{id}/retry-erp`        | Retry failed ERP push                      | 202     |
| POST   | `/api/invoices/{id}/complete`         | Advance to next workflow step              | 200     |

### Read Endpoints

| Method | Path                              | Purpose                          |
|--------|-----------------------------------|----------------------------------|
| GET    | `/api/invoices/{id}/workflow`     | Workflow instance + steps        |
| GET    | `/api/invoices/{id}/audit-log`    | Full audit trail                 |
| GET    | `/api/invoices/{id}/extraction`   | AI extraction results            |

### Existing Endpoint Modifications

- `PATCH /api/invoices/{id}` — Reject when status is `sent_to_erp` or `sending_to_erp`.
- `DELETE /api/invoices/{id}` — Reject when status is `sent_to_erp` or `sending_to_erp`.

## New Files

| File                              | Purpose                                   |
|-----------------------------------|-------------------------------------------|
| `backend/app/api/workflow.py`     | Workflow action endpoints                 |
| `backend/app/schemas/workflow.py` | Request/response schemas for actions      |
| `backend/app/services/__init__.py`| Services package                          |
| `backend/app/services/workflow_engine.py` | State machine and transition logic |
| `backend/app/services/storage.py` | S3/MinIO file upload                      |
| `backend/app/services/extraction.py` | AI invoice extraction                  |
| `backend/app/services/review.py`  | Review approve/reject/resubmit logic      |
| `backend/app/services/erp.py`     | ERP integration client with retry         |
| `backend/app/services/audit.py`   | Audit log helper                          |

## Implementation Order

1. **Foundation** — State machine, audit service, extend InvoiceStatus enum.
2. **Upload** — Storage service, extraction service (mock initially), upload endpoint.
3. **Review** — Approve/reject/assign/resubmit endpoints.
4. **ERP** — ERP client, send/retry endpoints.
5. **Polish** — Guards on existing endpoints, read endpoints, frontend UI.
