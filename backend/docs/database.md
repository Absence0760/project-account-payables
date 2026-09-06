# Database

PostgreSQL 16 running in Docker, accessed via async SQLAlchemy 2 with asyncpg.

## Multi-Database Architecture

The app uses a **database-per-tenant** isolation model:

| Database            | Purpose                    | Contains                                                                                    |
|---------------------|----------------------------|---------------------------------------------------------------------------------------------|
| `feohledger`  | Control plane              | organizations, users, roles, user_roles, email_verifications, plans, subscriptions, api_keys, assistant_usage |
| `feoh_acme`           | Acme Corp tenant           | invoices, vendors, payments, workflows, vendor_extraction_priors, invoice_embeddings, extraction_usage, card_rebates, ... |
| `feoh_techflow`       | TechFlow Inc tenant        | invoices, vendors, payments, workflows, vendor_extraction_priors, invoice_embeddings, ...   |

All databases run on the same PostgreSQL instance. Tenant database URLs are derived from the control-plane URL by swapping the database name.

**The two billing meters are TENANT tables**, despite being a per-org concern:
`extraction_usage` and `card_rebates` are absent from
`tenant_provisioning.CONTROL_TABLES`, no Alembic revision creates them in the
control plane, and `services/billing`'s `rollup_usage` reads them per tenant.
This table said otherwise for a long time, and `services/extraction.py` was
written against the claim — the resulting INSERT against a table the control
plane does not have raised into the extraction handler and marked successful
invoices `failed`. `assistant_usage`, by contrast, genuinely *is* control-plane.

## Connection

Default control-plane connection string (configured via `FEOH_DATABASE_URL`):

```
postgresql+asyncpg://postgres:postgres@localhost:5432/feohledger
```

Tenant connections are derived automatically:

```
postgresql+asyncpg://postgres:postgres@localhost:5432/feoh_acme
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
- `invoices` — master record (invoice_number, vendor_name, amount, currency, due_date, status, file_url, vendor_address, vendor_tax_id, ship_to_address, tax_rate, payment_method, reference_number, assigned_to_id, assigned_to, approved_by, rejected_by, **warnings** JSONB, **po_match** JSONB, **meta** JSONB)
  - `meta` is a free-form per-invoice metadata bag. Currently holds the cached audit-log summary under `meta["audit_summary"]` = `{text, confidence_context, source_fingerprint: {count, last_at}, generated_at, model}` — regenerated lazily when the audit-log fingerprint changes. No PII / banking data. See [`audit-summary.md`](audit-summary.md).
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
FEOH_MIGRATE_TENANT=feoh_acme alembic upgrade head
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
| 0022     | Tenant  | `vendors`    | Creates `vendor_change_requests` (supplier-portal staged bank/tax changes pending AP approval) + a partial index on `status='pending'`. |

> Migration 0022 may need renumbering or a merge revision at integration time — sibling features built on parallel branches may also claim `0022` (all branch off `0021_scim_bearer_hash`).

The `vendor_change_requests` table (tenant DB): `id`, `vendor_id` (FK → `vendors`, ON DELETE CASCADE), `organization_id`, `requested_by_vendor_user_id`, `change_type` (`bank_details` | `tax_id`), `status` (`pending` | `approved` | `rejected`), `proposed_value` (JSONB — banking PII, never logged), `reviewed_by_user_id`, `reviewed_at`, `review_note`, `created_at`, `updated_at`. Bank/tax changes from the supplier portal stage a row here instead of mutating the vendor; AP approval applies them. See `docs/supplier-portal.md`.

### Index coverage on `invoices`

Two of `invoices`' index-bearing columns exist for the procurement budget
rollup rather than for any list endpoint: a budget attributes realised spend by
an equality against one of `cost_center` / `gl_account` / `department` /
`project` (`services/budget_service._DIMENSION_MATCH_COLUMN`), so an unindexed
one seq-scans the whole invoice table on the `GET /budgets/check` path.

`department` / `project` were indexed by migration `0044`; `cost_center` /
`gl_account` predate it and were not, until
`0090_invoice_budget_dimension_indexes`. All four now carry
`ix_invoices_<column>` — SQLAlchemy's default single-column name, which is what
`create_all` produces, so a tenant provisioned by
`tenant_provisioning._create_tenant_tables` and one brought up by Alembic end
up with identical schemas. The measurement that justified 0090 (and the case
it deliberately does *not* improve) is in
[`procurement-budgets.md`](procurement-budgets.md) § Index coverage on the four
dimensions.

Both revisions gate on the `invoices` table existing, so they no-op on the
control plane and fan out to every tenant DB via
`scripts/migrate_all_tenants.py`.

### Index coverage on list + audit reads

Two query shapes ran against unindexed columns until
`0092_list_and_audit_indexes`, and both hit tables that only grow.

**`audit_log` — the fastest-growing table in the schema.** Nothing was indexed
on `created_at` or `action`, so five callers seq-scanned the whole table:

| Caller | Predicate | Now served by |
|---|---|---|
| `GET /api/audit/verify-signatures` (SOX signature sweep) | `action = 'invoice.approved' AND created_at` range | `ix_audit_log_action_created_at` |
| `GET /api/audit/export` (auditor date-range export) | `created_at` range `ORDER BY created_at` | `ix_audit_log_created_at` |
| `api/dashboard.py` approval-timing leg (every dashboard load) | `entity_type='invoice' AND action='invoice.approved'` | `ix_audit_log_action_created_at` |
| `api/adaptive_workflows.py` feedback reads | `action IN (…) AND created_at >= :since` | `ix_audit_log_action_created_at` |
| `services/retention_sweep.py` overdue count | `created_at < :cutoff`, and `… AND shipped_at IS NULL` | `ix_audit_log_created_at` / `ix_audit_log_shipped_at_null` |

A sixth — `services/audit_log_shipper._ship_tenant`, `shipped_at IS NULL ORDER BY
created_at ASC LIMIT n`, **per tenant every 60 s, returning nothing on a healthy
platform** — was a full scan only on tenants provisioned by `create_all`.
Migration `0010_audit_log_shipping` built `ix_audit_log_shipped_at_null` for it,
but nothing declared it on `AuditLog`, and fresh tenants never run Alembic. So a
migrated tenant had the index and a freshly-provisioned one did not, for as long
as audit shipping has existed. 0092 fixes that by declaring it on the model and
restating the (idempotent) `CREATE`; it deliberately does **not** drop it on
downgrade, and must never rename it — a second identical partial index would be
pure write overhead on every audit row forever.

That partial predicate is what keeps the index to the tail the shipper still has
work in: **8 KB** on a caught-up 1.2 M-row trail, against 50 MB for
`(action, created_at)` and 26 MB for `(created_at)`. Measured on a
`create_all`-provisioned tenant, the caught-up tick goes from 39.7 ms / 30 003
buffers to 0.040 ms / 1.

**List endpoints ordered by an unindexed column.** Page 1 of every list view was
a whole-table read plus a top-N heapsort — a cost that grows with the table
while the page size stays at 20. Seven tables get a plain ordering index and a
status-leading composite:

| Table | Ordering index | Status composite |
|---|---|---|
| `invoices` | `ix_invoices_created_at_id` | `ix_invoices_status_created_at_id` |
| `payments` | `ix_payments_created_at_id` | `ix_payments_status_created_at_id` |
| `exceptions` | `ix_exceptions_created_at_id` | `ix_exceptions_status_created_at_id` |
| `expenses` | `ix_expenses_created_at_id` | `ix_expenses_status_created_at_id` |
| `purchase_orders` | `ix_purchase_orders_created_at_id` | `ix_purchase_orders_status_created_at_id` |
| `contracts` | `ix_contracts_created_at_id` | `ix_contracts_status_created_at_id` |
| `corporate_card_transactions` | `ix_corp_card_txn_date_id` | `ix_corp_card_recon_status_txn_date_id` |

`corporate_card_transactions` is the odd one out **by design**: its list orders
by `txn_date DESC` (the date the charge happened, not the date we imported it)
and filters on `reconciliation_status`, so an index on `created_at` there would
never be read.

The composite is not redundant with the ordering index. For a *rare, scattered*
status Postgres otherwise walks the ordering index discarding non-matching rows
until it has 20 — 18 928 discarded for one page at 200 k invoices — and that
discard count grows with the table. Measured at 200 k invoices: list page 1
15.8 ms / 4 952 buffers → 0.031 ms / 4; a 0.1 %-selective status chip 2.1 ms /
591 buffers → 0.036 ms / 4.

Measured and deliberately **excluded**: `GET /api/invoices/counts` (the status
chips) and the list's own `SELECT count(*)`, both rollups over 100 % of the
filtered population where a seq scan is the right plan (the same conclusion 0090
reached for the budget rollup); and the `search=` path, whose `ILIKE '%term%'`
leading wildcard no btree index can serve — only `pg_trgm` would, at its own
write cost, which is a separate decision.

**No `CREATE INDEX CONCURRENTLY`**, deliberately: it cannot run inside Alembic's
transaction, and combined with `IF NOT EXISTS` it is a trap — a failed or
cancelled concurrent build leaves an *INVALID* index whose name then makes every
later run skip it and report success. The whole revision builds in ~2.5 s across
all eight tables at the volumes above (longest single index 742 ms on 1.2 M audit
rows), and that is the `ACCESS EXCLUSIVE` window. An operator whose `audit_log`
is large enough for that to matter can build the indexes by hand with
`CONCURRENTLY`, verify `pg_index.indisvalid` on each, and then run the migration
— every statement is `IF NOT EXISTS`, so it becomes a no-op.

Every index is declared **twice on purpose**: in the migration (existing tenants)
and in the owning model's `__table_args__` (fresh tenants, which are built by
`create_all` in `tenant_provisioning`, not by Alembic). Skipping the second half
is what left `ix_audit_log_shipped_at_null` missing from provisioned tenants for
82 revisions, so `tests/test_list_and_audit_indexes.py` is the guard — it fails
if the two spellings ever disagree on a name, a column order, or a partial
predicate, and it asserts each index actually serves its caller's query.

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
