# Accounts Payables App — Implementation Plan

## Context

A multi-tenant SaaS accounts payables platform that ingests invoices from multiple sources, extracts structured data using a hybrid extraction pipeline (AI, OCR, third-party), and routes those invoices through configurable approval, matching, exception, and payment workflows. Built with SvelteKit (frontend), FastAPI (backend), and PostgreSQL.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend | SvelteKit + TypeScript (pnpm) |
| Backend | Python 3.12 + FastAPI (async) |
| Database | PostgreSQL 16 |
| Auth | OAuth2 via Google + Microsoft (via `authlib`) |
| Task Queue | Celery + Redis (async jobs: email polling, extraction, notifications) |
| Storage | S3-compatible object store (MinIO locally, S3 in prod) |
| Dev Infra | Docker Compose (all services) |
| Package Mgr | pnpm (frontend), uv (backend) |

---

## System Architecture

```
┌──────────────────────────────────────────┐
│              SvelteKit Frontend           │
│  (Dashboard, Workflows, Invoice Views)    │
└────────────────┬─────────────────────────┘
                 │ REST / SSE
┌────────────────▼─────────────────────────┐
│           FastAPI Backend                 │
│  ┌─────────┐ ┌──────────┐ ┌───────────┐  │
│  │ Ingestion│ │Extraction│ │ Workflow  │  │
│  │ Service  │ │ Pipeline │ │  Engine   │  │
│  └─────────┘ └──────────┘ └───────────┘  │
│  ┌─────────┐ ┌──────────┐ ┌───────────┐  │
│  │  Auth   │ │ Matching │ │ Payment   │  │
│  │  (SSO)  │ │ Service  │ │ Scheduler │  │
│  └─────────┘ └──────────┘ └───────────┘  │
└────────┬────────────┬─────────────────────┘
         │            │
  ┌──────▼───┐  ┌─────▼──────┐  ┌──────────┐
  │PostgreSQL│  │   Redis     │  │  MinIO/  │
  │          │  │ (queue/cache│  │  S3      │
  └──────────┘  └─────────────┘  └──────────┘
```

---

## Multi-Tenancy Strategy

- Every table carries an `organization_id` foreign key (row-level tenancy).
- PostgreSQL Row-Level Security (RLS) policies enforce isolation at the DB layer.
- FastAPI middleware injects `org_id` from the JWT/session on every request.
- Celery tasks always carry `org_id` in their payload.

---

## Core Data Models

### Organizations & Users
- `organizations` — tenant record (name, settings, plan)
- `users` — org members (SSO provider ID, email, `organization_id`)
- `roles` — AP Clerk, AP Manager, CFO, Admin
- `user_roles` — many-to-many

### Invoice Pipeline
- `invoices` — master record (status, org, vendor, amounts, currency, due_date, raw file ref)
- `invoice_line_items` — individual lines (description, qty, unit_price, tax)
- `invoice_extraction_results` — raw extraction output per attempt (method used, confidence, JSON blob)

### Procurement (for 3-way matching)
- `purchase_orders` — PO header (vendor, total, status)
- `po_line_items` — PO lines
- `goods_receipts` — GR header
- `gr_line_items` — GR lines

### Workflow Engine
- `workflow_definitions` — configurable per org (steps, rules, conditions as JSON)
- `workflow_instances` — one per invoice (current step, state machine)
- `workflow_steps` — individual step records (assigned_to, action, timestamps)
- `audit_log` — immutable event log (actor, action, entity, timestamp)

### Payments
- `payment_runs` — batch payment execution records
- `payment_schedules` — due dates, early-pay discount windows per invoice
- `payments` — individual payment records (amount, method, status, ref)

### Exceptions
- `exceptions` — flagged issues (duplicate, mismatch, anomaly, type, resolution status)

---

## Module Breakdown

### 1. Ingestion Service
Handles all inbound invoice sources:
- **PDF/Image Upload** — multipart upload endpoint → store in S3 → enqueue extraction job
- **Email Ingestion** — Celery beat polls configured IMAP/Gmail/Outlook mailboxes per org, downloads attachments, enqueues extraction
- **API Integration** — webhook endpoint or pull-based connector (REST) for ERP/vendor portal inbound invoices
- **Manual Entry** — REST endpoint for structured form submissions (skips extraction)

### 2. Extraction Pipeline
Pluggable, priority-ordered extraction:
1. **AI/LLM (Claude API)** — primary extractor; send PDF text/image to Claude, return structured JSON (vendor, date, total, line items, tax, PO ref, etc.)
2. **Third-party OCR** — fallback to AWS Textract or Google Document AI when confidence is low
3. **Rules-based OCR** — template-matched extraction using Tesseract + regex as final fallback

Each attempt stores results in `invoice_extraction_results` with a confidence score. The pipeline selects the highest-confidence result. Humans can correct and re-trigger.

### 3. Workflow Engine
Configurable state machine per org:
- Workflow definitions stored as JSON (steps, conditions, assignees, SLAs)
- On invoice creation: instantiate a `workflow_instance`, create first `workflow_step`
- Step types: `approve`, `review`, `match`, `flag_exception`, `schedule_payment`
- Transitions triggered by API actions (approve, reject, escalate, override)
- SLA timers via Celery beat — escalate or notify on breach
- Full audit trail written on every transition

### 4. 3-Way Matching Service
- On demand or automatic at a workflow step
- Match `invoice` → `purchase_order` (by PO number on invoice) → `goods_receipt`
- Tolerance rules per org (e.g. ±2% variance acceptable)
- Mismatches → create `exception` record and redirect workflow to exception step

### 5. Exception Handling
- Exception types: duplicate invoice, PO mismatch, quantity mismatch, price variance, missing PO, anomaly (statistical outlier)
- Each exception has a resolution workflow (override with reason, send back to vendor, reject)
- Exception dashboard with filter/sort/bulk actions

### 6. Payment Scheduling
- Payment terms parsed from invoice (net 30, 2/10 net 30, etc.)
- `payment_schedules` tracks due date and early-pay discount window
- Payment run batching: AP Manager selects invoices, confirms payment run
- Payment status tracking: pending → submitted → cleared/failed
- Integration hooks for bank/ERP (pluggable adapter interface)

### 7. Auth & Multi-Tenancy
- SSO via Google and Microsoft OAuth2 (`authlib` with FastAPI)
- On first login: prompt org selection or org creation
- JWT issued by backend (short-lived access + refresh tokens)
- RBAC enforced in FastAPI dependencies (`Depends(require_role("manager"))`)

---

## API Structure (FastAPI)

```
/auth
  POST /auth/login/{provider}            # initiate OAuth
  GET  /auth/callback/{provider}         # OAuth callback
  POST /auth/refresh                     # refresh JWT

/invoices
  GET    /invoices                       # list (filterable, paginated)
  POST   /invoices                       # manual create or upload
  GET    /invoices/{id}                  # detail
  POST   /invoices/{id}/extract          # re-trigger extraction
  PATCH  /invoices/{id}                  # correct extracted data

/workflow
  GET    /workflow/definitions           # list org's workflow configs
  POST   /workflow/definitions           # create workflow definition
  GET    /workflow/instances/{id}        # instance state
  POST   /workflow/instances/{id}/action # approve/reject/escalate

/matching
  POST   /matching/run/{invoice_id}      # trigger 3-way match

/exceptions
  GET    /exceptions                     # list
  POST   /exceptions/{id}/resolve        # resolve with reason

/payments
  GET    /payments/schedule              # upcoming due dates
  POST   /payments/runs                  # create payment run
  GET    /payments/runs/{id}             # run status

/vendors
  # CRUD for vendor master data

/admin
  # User management, org settings, workflow config, audit log
```

---

## Frontend Structure (SvelteKit)

```
src/
  routes/
    (auth)/           # login, OAuth callback
    (app)/
      dashboard/      # KPI cards, invoice queue, exception count
      invoices/       # list, detail, upload, correction UI
      workflows/      # workflow builder, active instances
      matching/       # 3-way match review
      exceptions/     # exception queue
      payments/       # payment schedule, run management
      vendors/        # vendor master
      admin/          # users, roles, org settings
  lib/
    api/              # typed fetch wrappers
    stores/           # Svelte stores (auth, org context)
    components/       # shared UI components
```

---

## Phased Delivery

### Phase 1 — Foundation
- Docker Compose setup (FastAPI, PostgreSQL, Redis, MinIO)
- Multi-tenant DB schema + RLS
- SSO auth (Google + Microsoft)
- Invoice upload + PDF storage
- AI extraction (Claude API) + manual correction UI
- Basic invoice list/detail views

### Phase 2 — Workflow Engine
- Workflow definition builder (UI + API)
- Approval routing (multi-step, assignee rules)
- Email notifications (Celery + SMTP)
- Audit log

### Phase 3 — Matching & Exceptions
- PO and GR data model + ingestion
- 3-way matching engine
- Exception creation, queue, and resolution workflow

### Phase 4 — Ingestion Expansion
- Email ingestion (IMAP/Gmail/Outlook poller)
- ERP/vendor API webhook receiver
- Fallback OCR pipeline (Textract / Tesseract)

### Phase 5 — Payments & Reporting
- Payment scheduling and due-date tracking
- Payment run management
- Dashboard KPIs and reporting views
- Early-pay discount alerts

---

## Verification Approach

- **Unit tests**: pytest for extraction pipeline logic, matching rules, workflow state machine
- **Integration tests**: FastAPI `TestClient` against a real PostgreSQL test DB (no mocks)
- **Frontend tests**: Playwright for critical flows (login, invoice upload, approval)
- **Manual smoke test per phase**: Docker Compose up → upload invoice → verify extraction → approve → check audit log
- **Multi-tenancy test**: Two orgs, confirm data isolation via API responses and direct DB query
