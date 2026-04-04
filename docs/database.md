# Database

PostgreSQL 16 running in Docker, accessed via async SQLAlchemy 2 with asyncpg.

## Connection

Default connection string (configured via `AP_DATABASE_URL`):

```
postgresql+asyncpg://postgres:postgres@localhost:5432/account_payables
```

The database is started as part of the Docker Compose stack:

```bash
cd backend
docker compose up -d postgres
```

## Data Models

### Organizations & Users
- `organizations` — tenant record (name, slug, settings as JSONB, plan)
- `users` — org members (email, full_name, hashed_password, SSO provider fields)
- `roles` — AP Clerk, AP Manager, CFO, Admin
- `user_roles` — many-to-many join table

### Invoice Pipeline
- `invoices` — master record (invoice_number, vendor_name, amount, currency, due_date, status, file_url)
- `invoice_line_items` — individual lines (description, qty, unit_price, tax, total)
- `invoice_extraction_results` — AI extraction output per attempt (method, confidence, raw JSON)

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
- `payment_schedules` — due dates, early-pay discount windows
- `payments` — individual payment records (amount, method, status, ref)

### Exceptions
- `exceptions` — flagged issues (duplicate, mismatch, anomaly, resolution status)

## Migrations (Alembic)

Generate a new migration after model changes:

```bash
cd backend
source .venv/bin/activate
alembic revision --autogenerate -m "description of change"
```

Apply migrations:

```bash
alembic upgrade head
```

Downgrade:

```bash
alembic downgrade -1
```

## Seeding

Run the seed script to create tables and insert demo data:

```bash
python scripts/seed.py
```

This uses `Base.metadata.create_all()` to create tables (safe to re-run) and inserts demo data with fixed UUIDs for reproducibility.

## Multi-Tenancy

Every data table carries an `organization_id` foreign key. Row-Level Security (RLS) policies are planned to enforce isolation at the database layer. Currently, tenant filtering is handled in the FastAPI API layer via the JWT-decoded `org_id`.
