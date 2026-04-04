# Database

PostgreSQL 16 running in Docker, accessed via async SQLAlchemy 2 with asyncpg.

## Multi-Database Architecture

The app uses a **database-per-tenant** isolation model:

| Database            | Purpose                    | Contains                                    |
|---------------------|----------------------------|---------------------------------------------|
| `account_payables`  | Control plane              | organizations, users, roles, user_roles     |
| `ap_acme`           | Acme Corp tenant           | invoices, vendors, payments, workflows, ... |
| `ap_techflow`       | TechFlow Inc tenant        | invoices, vendors, payments, workflows, ... |

All databases run on the same PostgreSQL instance. Tenant database URLs are derived from the control-plane URL by swapping the database name.

## Connection

Default control-plane connection string (configured via `AP_DATABASE_URL`):

```
postgresql+asyncpg://postgres:postgres@localhost:5432/account_payables
```

Tenant connections are derived automatically:

```
postgresql+asyncpg://postgres:postgres@localhost:5432/ap_acme
```

## Data Models

### Control-Plane Tables

- `organizations` — tenant registry (name, slug, db_name, settings, plan)
- `users` — all users across all tenants (email, full_name, hashed_password, organization_id)
- `roles` — role definitions (admin, ap_manager, ap_clerk, cfo)
- `user_roles` — many-to-many join table

### Tenant Tables

#### Invoice Pipeline
- `invoices` — master record (invoice_number, vendor_name, amount, currency, due_date, status, file_url)
- `invoice_line_items` — individual lines (description, qty, unit_price, tax, total)
- `invoice_extraction_results` — AI extraction output per attempt (method, confidence, raw JSON)

#### Procurement (for 3-way matching)
- `purchase_orders` — PO header (vendor, total, status)
- `po_line_items` — PO lines
- `goods_receipts` — GR header
- `gr_line_items` — GR lines

#### Workflow Engine
- `workflow_definitions` — configurable per org (steps, rules, conditions as JSON)
- `workflow_instances` — one per invoice (current step, state machine)
- `workflow_steps` — individual step records (assigned_to, action, timestamps)
- `audit_log` — immutable event log (actor, action, entity, timestamp)

#### Payments
- `payment_runs` — batch payment execution records
- `payment_schedules` — due dates, early-pay discount windows
- `payments` — individual payment records (amount, method, status, ref)

#### Exceptions
- `exceptions` — flagged issues (duplicate, mismatch, anomaly, resolution status)

Tenant tables have an `organization_id` column (plain UUID, no foreign key) for backward compatibility.

## Migrations (Alembic)

### Control-plane DB

```bash
cd backend
source .venv/bin/activate
alembic upgrade head
```

### Single tenant DB

```bash
AP_MIGRATE_TENANT=ap_acme alembic upgrade head
```

### All tenant DBs

```bash
python scripts/migrate_all_tenants.py
```

### Generate a new migration

```bash
alembic revision --autogenerate -m "description of change"
```

### Downgrade

```bash
alembic downgrade -1
```

## Seeding

Seed the control plane and both demo tenants:

```bash
python scripts/seed.py
```

This creates databases, tables, and inserts demo data. Safe to re-run (idempotent).

## Provisioning New Tenants

```bash
python scripts/create_tenant.py \
  --name "New Corp" --slug newcorp \
  --admin-email admin@newcorp.com --admin-password changeme
```

This creates the database, tables, org record, and admin user in one step.
