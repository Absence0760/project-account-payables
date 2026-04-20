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

The state machine in `services/workflow_engine.py` enforces the transitions documented below. The `posted_in_erp`, `payment_scheduled`, and `paid` statuses are set by **external events** (inbound ERP webhook, payment-run execution) rather than the standard transition path; they live in the enum so the status badge is accurate but they're not part of the linear workflow flow.

## Status Transitions

```
new ──────────────> pending                  (file uploaded, extraction triggered)
pending ──────────> ready_for_review         (extraction succeeded)
pending ──────────> failed                   (extraction failed)
ready_for_review ─> approved                 (reviewer approves)
ready_for_review ─> rejected                 (reviewer rejects)
rejected ─────────> ready_for_review         (re-submitted after edits)
rejected ─────────> new                      (requires full re-upload)
approved ─────────> sending_to_erp           (ERP submission initiated)
approved ─────────> done                     (no ERP step in workflow → terminal)
sending_to_erp ───> sent_to_erp              (ERP confirmed)
sending_to_erp ───> failed                   (ERP rejected or timed out)
sent_to_erp ──────> done                     (terminal — workflow complete)
new ──────────────> done                     (no workflow steps enabled → terminal)
failed ───────────> pending                  (retry extraction)
failed ───────────> sending_to_erp           (retry ERP push, if previously approved)
```

`done` is the terminal state for the standard workflow path. `posted_in_erp` / `payment_scheduled` / `paid` are set by external triggers (ERP webhook, payment runs) and are not driven by `transition_invoice()`.

All transitions are enforced by a state machine in `services/workflow_engine.py`. Invalid transitions return `409 Conflict`.

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

Multiple approvers can be configured for the "specific" strategy via the workflow editor's search-and-pick interface.

### Who Can Review

- If an invoice has an `assigned_to_id`, only that user sees Approve/Reject buttons in the modal.
- If no one is assigned, any user with a non-clerk role can review.
- AP Clerks never see review buttons regardless of assignment.

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

One per organization. Represents the workflow template. The `steps_config` JSONB column defines the step sequence:

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
