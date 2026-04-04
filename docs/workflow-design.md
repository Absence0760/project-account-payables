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

| Status             | Meaning                                  | Workflow Stage      |
|--------------------|------------------------------------------|---------------------|
| `new`              | Created, no file attached yet            | Pre-workflow        |
| `pending`          | File uploaded, AI extraction in progress | Stage 1: Upload     |
| `ready_for_review` | Extraction complete, awaiting reviewer   | Stage 2: Review     |
| `approved`         | Reviewer approved, ready for ERP push    | Between 2 and 3     |
| `rejected`         | Reviewer rejected, needs edits/re-upload | Stage 2 (branch)    |
| `sending_to_erp`   | Async ERP submission in flight           | Stage 3: ERP Send   |
| `sent_to_erp`      | ERP confirmed receipt — terminal state   | Stage 4: Done       |
| `failed`           | Any stage failed                         | Error state         |

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
sending_to_erp ───> sent_to_erp              (ERP confirmed)
sending_to_erp ───> failed                   (ERP rejected or timed out)
failed ───────────> pending                  (retry extraction)
failed ───────────> sending_to_erp           (retry ERP push, if previously approved)
```

All transitions are enforced by a state machine in `services/workflow_engine.py`. Invalid transitions return `409 Conflict`.

## Stage 1: Upload & Extraction

**Endpoint:** `POST /api/invoices/upload` (multipart/form-data)

1. Accept file upload (PDF, PNG, JPEG, TIFF — max 25 MB).
2. Store file in S3/MinIO under `{organization_id}/{invoice_id}/{filename}`.
3. Create the Invoice record with `status=new` and placeholder fields (`invoice_number="PENDING"`, `amount=0`). Populate `file_key` and `file_url`.
4. Create a WorkflowInstance and the first WorkflowStep (`type=upload`).
5. Transition invoice to `pending`.
6. Dispatch async AI extraction task.
7. Return `202 Accepted` with the invoice ID and `correlation_id`.

**Extraction outcomes:**
- **Success:** Extracted fields are written to the invoice, an `InvoiceExtractionResult` row is created with the confidence score and raw output, and the invoice transitions to `ready_for_review`.
- **Failure:** Invoice transitions to `failed`. Error details are stored in `WorkflowInstance.state_data`.
- **Timeout:** If extraction has not completed within 5 minutes, the invoice is transitioned to `failed` with reason `extraction_timeout`.

## Stage 2: Review

A human reviewer examines the extracted data and either approves or rejects the invoice.

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
2. Transition to `rejected`.
3. Complete the review WorkflowStep with `action=rejected`.
4. Store rejection reason in the audit log details.
5. Write audit log (`invoice.rejected`).

### Resubmit

After a rejection, the user can edit the invoice fields and resubmit for another round of review. This transitions `rejected → ready_for_review` and creates a new review WorkflowStep.

A rejection counter is tracked in `WorkflowInstance.state_data`. After a configurable number of rejections, the invoice may be auto-escalated or locked.

## Stage 3: Send to ERP

**Endpoint:** `POST /api/invoices/{id}/send-to-erp`

Can be triggered manually by the user or automatically after approval.

1. Validate invoice status is `approved`.
2. Transition to `sending_to_erp`.
3. Create a WorkflowStep (`type=erp_push`).
4. Dispatch async ERP call. The invoice's `correlation_id` is sent as an idempotency key to prevent duplicate records in the ERP system.

### Retry Logic

| Scenario             | Behavior                                                                 |
|----------------------|--------------------------------------------------------------------------|
| Transient failure    | Retry with exponential backoff, up to 3 attempts. Stay in `sending_to_erp`. |
| Permanent failure    | Transition to `failed`. Record error in WorkflowStep and audit log.     |
| Success              | Transition to `sent_to_erp`. Store ERP reference ID in `state_data`.    |

**Manual retry:** `POST /api/invoices/{id}/retry-erp` — only valid when status is `failed` and the invoice was previously approved (i.e., `approved_by` is set).

## Stage 4: Done

`sent_to_erp` is the terminal state. When the ERP confirms receipt:

1. Complete the `erp_push` WorkflowStep.
2. Create a final WorkflowStep (`type=done`, `action=completed`).
3. Set `WorkflowInstance.state = "completed"`.
4. Write audit log (`invoice.erp_confirmed`).

The invoice is now **immutable**. The PATCH and DELETE endpoints reject requests for invoices in `sent_to_erp` or `sending_to_erp` status.

## Workflow Models

### WorkflowDefinition

One per organization. Represents the workflow template. The `steps_config` JSONB column defines the step sequence:

```json
{
  "steps": [
    { "number": 1, "type": "upload",   "name": "Upload & Extract" },
    { "number": 2, "type": "review",   "name": "Human Review" },
    { "number": 3, "type": "erp_push", "name": "Send to ERP" },
    { "number": 4, "type": "done",     "name": "Complete" }
  ]
}
```

Seeded per tenant at organization creation. Configurable for future custom approval chains.

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
| `step_type`    | `upload`, `review`, `erp_push`, `done`              |
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

Every state transition writes an audit log entry via `services/audit.py`. Each entry records:

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
| Extraction timeout          | 5-minute limit; auto-transition to `failed`                          |
| Orphaned `pending` invoices | Background sweep transitions to `failed` after N minutes             |
| Orphaned `sending_to_erp`   | Same sweep for invoices stuck beyond the retry window                |
| Duplicate invoices          | Check `invoice_number` + `vendor_name` per org; return `409 Conflict`|
| Rejection loops             | Track count in `state_data`; auto-escalate after N rejections        |
| File validation             | Max 25 MB; allowed types: PDF, PNG, JPEG, TIFF                      |
| ERP idempotency             | `correlation_id` sent as idempotency key to the ERP                  |

## API Endpoints Summary

### Workflow Actions

| Method | Path                              | Purpose                     | Returns |
|--------|-----------------------------------|-----------------------------|---------|
| POST   | `/api/invoices/upload`            | Upload file, start workflow | 202     |
| POST   | `/api/invoices/{id}/assign`       | Assign reviewer             | 200     |
| POST   | `/api/invoices/{id}/approve`      | Approve invoice             | 200     |
| POST   | `/api/invoices/{id}/reject`       | Reject invoice              | 200     |
| POST   | `/api/invoices/{id}/resubmit`     | Resubmit for review         | 200     |
| POST   | `/api/invoices/{id}/send-to-erp`  | Initiate ERP push           | 202     |
| POST   | `/api/invoices/{id}/retry-erp`    | Retry failed ERP push       | 202     |

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
