# Database

PostgreSQL 16 running in Docker, accessed via async SQLAlchemy 2 with asyncpg.

## Multi-Database Architecture

The app uses a **database-per-tenant** isolation model:

| Database            | Purpose                    | Contains                                                                                    |
|---------------------|----------------------------|---------------------------------------------------------------------------------------------|
| `account_payables`  | Control plane              | organizations, users, roles, user_roles, email_verifications, extraction_usage, card_rebates |
| `ap_acme`           | Acme Corp tenant           | invoices, vendors, payments, workflows, vendor_extraction_priors, invoice_embeddings, ...   |
| `ap_techflow`       | TechFlow Inc tenant        | invoices, vendors, payments, workflows, vendor_extraction_priors, invoice_embeddings, ...   |

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
- `users` — all users across all tenants. Columns: `email`, `full_name`, `hashed_password` (nullable for SSO-only), `organization_id`, `is_active`, `must_change_password`, `sso_provider` + `sso_provider_id` (OIDC linkage), `mfa_secret` + `mfa_enabled` + `mfa_enrolled_at` (TOTP MFA)
- `roles` — role definitions (admin, ap_manager, ap_clerk, cfo)
- `user_roles` — many-to-many join table
- `email_verifications` — pending self-service signups (token, email, slug, admin_name, expires_at, consumed_at). Created by `POST /api/signup/start`, consumed by `POST /api/signup/complete`.
- `extraction_usage` — billing rows: invoice_id, provider, program_type, period (used for tracking platform-extraction usage)
- `card_rebates` — billing rows: virtual_card_id, amount, rate, status, period

### Tenant Tables

#### Invoice Pipeline
- `invoices` — master record (invoice_number, vendor_name, amount, currency, due_date, status, file_url, vendor_address, vendor_tax_id, ship_to_address, tax_rate, payment_method, reference_number, assigned_to_id, assigned_to, approved_by, rejected_by)
- `invoice_line_items` — individual lines (description, qty, unit_price, tax, total)
- `invoice_extraction_results` — AI extraction output per attempt (method, confidence, raw JSON, **priors_metadata** — summary of vendor cache overrides + RAG neighbors applied during extraction)
- `invoice_embeddings` — pgvector `vector(1536)` RAG store. One row per approved invoice; corrected_fields snapshot + embedding. Queried via cosine distance for few-shot retrieval. See [`ai-extraction.md`](ai-extraction.md).

#### Extraction priors
- `vendor_extraction_priors` — per-vendor correction cache. Unique on `(vendor_id, field_name)`. Populated when reviewers correct fields during approval; applied to low-confidence extractions for the same vendor.

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

### pgvector

`invoice_embeddings.embedding` uses the `vector` data type from the [pgvector](https://github.com/pgvector/pgvector) Postgres extension. Both the local `docker-compose.yml` (`pgvector/pgvector:pg16` image) and `services.tenant_provisioning._create_tenant_tables` / `scripts.seed.create_tenant_tables` run `CREATE EXTENSION IF NOT EXISTS vector` before creating tenant tables so the column type resolves.

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

### Existing migrations

Migrations may target either the control plane or tenant DBs — never both. Each migration in `alembic/versions/` gates on presence of a table that only exists in its target shape (e.g., `users` for control, `vendors` for tenant) and no-ops on the wrong DB.

| Revision | Scope   | Gate table   | What it does                                                                                                             |
|----------|---------|--------------|--------------------------------------------------------------------------------------------------------------------------|
| 0001     | Control | `users`      | Adds `users.must_change_password`; creates `email_verifications`.                                                        |
| 0002     | Tenant  | `vendors`    | Creates `vendor_extraction_priors` (per-vendor correction cache).                                                        |
| 0003     | Tenant  | `invoices`   | Creates pgvector extension + `invoice_embeddings` + HNSW cosine index; adds `invoice_extraction_results.priors_metadata`. |
| 0004     | Control | `users`      | Adds `users.sso_provider` + `users.sso_provider_id` + partial index for OIDC user lookup.                                |
| 0005     | Control | `users`      | Adds `users.mfa_secret`, `users.mfa_enabled`, `users.mfa_enrolled_at` (TOTP MFA).                                        |
| 0006     | Tenant  | `invoices`   | Adds `invoices.po_match` JSONB column (latest 2/3-way PO match result).                                                  |
| 0007     | Tenant  | `payments`   | Adds `payments.provider`, `provider_payment_id`, `failure_reason`, `submitted_at`, `completed_at` (adapter lifecycle).   |

## Seeding

Seed the control plane and both demo tenants:

```bash
python scripts/seed.py
```

This creates databases, tables, and inserts demo data. Safe to re-run (idempotent). Seeds 6 users across 2 orgs with different roles (admin, AP manager, AP clerk, CFO).

To re-seed from scratch, drop the Docker volumes first: `docker compose down -v && docker compose up -d`

## Provisioning New Tenants

```bash
python scripts/create_tenant.py \
  --name "New Corp" --slug newcorp \
  --admin-email admin@newcorp.com --admin-password changeme
```

This creates the database, tables, org record, and admin user in one step.
