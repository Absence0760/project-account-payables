# Multi-Tenancy

The application uses **subdomain-based routing** with **database-per-tenant isolation**. Each tenant gets a unique subdomain and their own PostgreSQL database.

## Architecture

```
acme.localhost:7777 ──┐                        ┌── ap_acme DB (invoices, vendors, ...)
                      ├── Backend API :8000 ────┼── ap_techflow DB
techflow.localhost:7777┘   (shared)             └── account_payables DB (control plane)
```

- **Control-plane DB** (`account_payables`): organizations, users, roles
- **Tenant DBs** (`ap_<slug>`): invoices, vendors, payments, workflows, exceptions

## How It Works

### 1. Frontend extracts subdomain

`src/lib/tenant.ts` reads the subdomain from the browser URL:
- `acme.localhost:7777` → slug `acme`
- `techflow.app.com` → slug `techflow`
- `localhost:7777` (no subdomain) → shows "no tenant" page

### 2. Frontend sends tenant header

Every API request includes an `X-Tenant-Slug` header (added automatically by `src/lib/api.ts`).

### 3. Backend resolves tenant

The dependency chain in `app/tenant.py`:
```
X-Tenant-Slug header + Authorization Bearer <JWT>
  → get_tenant_slug()        # extract slug from header
  → get_tenant()             # look up org in control-plane DB by slug
                             # CROSS-CHECK: payload["org"] must equal org.id
                             #              for employee tokens (typ != "vendor")
                             #              → 403 on mismatch
  → get_tenant_db()          # yield a session on the tenant's DB
```

The cross-check in `get_tenant` is the load-bearing security control. Without it, an authenticated user from tenant A could read or mutate tenant B's data by sending `X-Tenant-Slug: <other-tenant>`. Vendor-portal tokens (`typ="vendor"`) are exempt — they're scoped naturally because `VendorUser` rows live in the per-tenant DB. See `docs/authentication.md § Cross-tenant guard`.

### Custom-domain fallback (white-label vanity hostnames)

When the `X-Tenant-Slug` header is **absent**, `get_tenant_slug` falls back to matching the request `Host` against the per-org `settings.brand.custom_domains` JSON array, so a tenant served on its own vanity hostname (`ap.acmecorp.com`) resolves to its slug without the SPA supplying the header. An unknown / unmatched host (or a malformed settings blob) falls back to the original `400` — never a wrong tenant. **The fallback only picks a *candidate* slug; the `get_tenant` JWT `org`-claim cross-check above still gates it**, so a forged `Host` header can no more widen access than a forged `X-Tenant-Slug` header can. See `docs/white-label.md § Custom domains` for the full trust model.

### 4. Routes use the correct database

- **Auth routes** (`/api/auth/*`) use the control-plane DB — users and orgs live there
- **Business routes** (`/api/invoices`, `/api/vendors`, `/api/dashboard`) use the tenant DB — data is fully isolated per tenant. Every route flows through `get_tenant` so the cross-check fires uniformly.

## Database Layout

### Control-plane DB (`account_payables`)

| Table                  | Purpose                                                          |
|------------------------|------------------------------------------------------------------|
| `organizations`        | Tenant registry (slug, db_name, plan, settings JSONB)            |
| `users`                | All users across all tenants — incl. SSO + MFA fields            |
| `roles`                | Role definitions (admin, ap_manager, ap_clerk, cfo)              |
| `user_roles`           | User-role assignments                                            |
| `email_verifications`  | Pending self-service signups (token, slug, expires_at)           |
| `extraction_usage`     | Per-invoice billing rows for platform extraction                 |
| `card_rebates`         | Per-virtual-card rebate billing rows                             |

### Tenant DB (`ap_<slug>`)

| Table                        | Purpose                    |
|------------------------------|----------------------------|
| `invoices`                   | Invoice records            |
| `invoice_line_items`         | Line-level detail          |
| `invoice_extraction_results` | AI extraction output       |
| `vendors`                    | Vendor master data         |
| `purchase_orders`            | PO headers                 |
| `po_line_items`              | PO line detail             |
| `goods_receipts`             | GR headers                 |
| `gr_line_items`              | GR line detail             |
| `workflow_definitions`       | Approval workflows         |
| `workflow_instances`         | Active workflow runs        |
| `workflow_steps`             | Individual step records     |
| `audit_log`                  | Immutable event log        |
| `payment_runs`               | Batch payment runs         |
| `payment_schedules`          | Payment timing             |
| `payments`                   | Individual payments        |
| `exceptions`                 | Flagged issues             |
| `credit_memos`               | Vendor credit memos        |
| `bank_statements`            | Uploaded statement files (for reconciliation) |
| `bank_transactions`          | Parsed transactions matched against payments |
| `sanctions_checks`           | Append-only KYC / sanctions screening trail |
| `scheduled_reports`          | Recurring CFO-report definitions |
| `invoice_embeddings`         | Vector embeddings for RAG + duplicate detection |
| `vendor_extraction_priors`   | Cached vendor field priors fed to next extraction |
| `vendor_users`               | Supplier-portal credentials (scoped to a Vendor) |
| `card_reveal_tokens`         | Single-use PAN-reveal tokens for vendors |

Tenant tables still have an `organization_id` column (as a plain UUID, not a foreign key) for backward compatibility.

## Provisioning a New Tenant

```bash
cd backend
source .venv/bin/activate
python scripts/create_tenant.py \
  --name "New Corp" \
  --slug newcorp \
  --admin-email admin@newcorp.com \
  --admin-password changeme
```

This:
1. Creates `ap_newcorp` database on the Postgres server
2. Inserts the org row into the control-plane DB
3. Creates the admin user in the control-plane DB
4. Creates all tenant tables in `ap_newcorp`

The new tenant is immediately accessible at `newcorp.localhost:7777`.

## Migrations

### Control-plane DB

```bash
cd backend
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

## CORS

The backend uses a regex-based CORS policy to allow any subdomain:

```python
allow_origin_regex=r"https?://([\w-]+\.)?(localhost(:\d+)?|app\.com)"
```

This accepts `acme.localhost:7777`, `techflow.localhost:7777`, `acme.app.com`, etc.

## Local Development

### DNS

`*.localhost` resolves to `127.0.0.1` natively in Chrome, Firefox, and Edge. No `/etc/hosts` changes needed.

For Safari, add entries manually:

```
# /etc/hosts
127.0.0.1 acme.localhost techflow.localhost
```

### Dev URLs

| Tenant    | URL                           | Login                          |
|-----------|-------------------------------|--------------------------------|
| Acme      | http://acme.localhost:7777    | `demo@acme.com` / `demo`      |
| TechFlow  | http://techflow.localhost:7777| `admin@techflow.com` / `demo`  |

### Docker

The `docker-compose.yml` mounts `init-tenants.sql` which auto-creates `ap_acme` and `ap_techflow` databases on first run. For a fresh start:

```bash
docker compose down -v
docker compose up -d
python scripts/seed.py
```

## Engine Pool Management

Each tenant gets a lazily-created `AsyncEngine` with `pool_size=5, max_overflow=10`. Engines are cached in `app/database._tenant_engines` and disposed on shutdown. For production with many tenants, consider adding a connection pooler like PgBouncer.
