# Backend — CLAUDE.md

Backend-specific guidance. See root `CLAUDE.md` for project-wide context.

## Where to look (backend docs)

Deep-dive docs live in `backend/docs/`:

| Topic | File |
|-------|------|
| REST API reference | `docs/api-reference.md` |
| PostgreSQL schema + migrations | `docs/database.md` |
| AI extraction adapters | `docs/ai-extraction.md` |
| ERP adapters (Merge.dev + direct) | `docs/erp-integration.md` |
| Workflow state machine | `docs/workflow-design.md` |
| Workflow snapshot semantics | `docs/workflow-snapshots.md` |
| Payment runs + ERP sync | `docs/payments.md` |
| Virtual cards (Lithic / Nium) | `docs/virtual-cards.md` |
| PO matching (2-way / 3-way) | `docs/po-matching.md` |
| Vendor management | `docs/vendor-management.md` |
| Local AI testing (Ollama) | `docs/local-ai-testing.md` |
| Docker Compose services | `docs/docker.md` |
| Redis | `docs/redis.md` |
| MinIO / S3 | `docs/minio.md` |

Cross-cutting topics (auth, multi-tenancy, deployment) live at the repo root `../docs/`.

## Stack

- **FastAPI** on **Python 3.12+**, async throughout
- **SQLAlchemy 2** async with asyncpg driver
- **Alembic** for migrations (supports per-tenant execution)
- **PostgreSQL 16**, **Redis 7**, **MinIO** (S3-compatible)
- **Pydantic v2** for request/response schemas
- **ruff** for lint/format (line-length 100, rules: E, F, I, UP)

## First-time setup (from `backend/`)

```bash
docker compose up -d                            # start Postgres, Redis, MinIO
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                         # install with dev deps
python scripts/seed.py                          # seed 2 demo tenants
python main.py                                  # dev server on :8000
```

## Commands (from `backend/`)

```bash
docker compose up -d          # Postgres, Redis, MinIO
python main.py                # dev server :8000 (auto-reload via uvicorn)
pytest                        # run tests
ruff check . && ruff format . # lint + format

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head                                # control plane
AP_MIGRATE_TENANT=ap_acme alembic upgrade head      # single tenant
python scripts/migrate_all_tenants.py               # all tenants
```

## Project structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app, CORS, router includes, lifespan
│   ├── config.py            # Pydantic Settings (AP_ prefix env vars)
│   ├── database.py          # Control engine + per-tenant engine pool
│   ├── redis.py             # Redis connection + token blocklist
│   ├── tenant.py            # X-Tenant-Slug → tenant DB session
│   ├── api/
│   │   └── deps.py          # JWT auth, get_current_user, get_org_id
│   ├── models/              # SQLAlchemy ORM models
│   ├── schemas/             # Pydantic request/response models
│   ├── routers/             # FastAPI routers (one per domain)
│   └── services/            # Business logic
│       ├── extraction_adapters/   # AI/OCR providers (pluggable)
│       ├── erp_adapters/          # ERP connectors (pluggable)
│       └── card_adapters/         # Virtual card providers (pluggable)
├── alembic/                 # Migration config + versions
├── scripts/                 # seed.py, create_tenant.py, migrate_all_tenants.py
├── docker-compose.yml       # Postgres, Redis, MinIO
└── pyproject.toml           # Dependencies, ruff config, pytest config
```

## Database architecture

**Two-database pattern:**

1. **Control plane** (`account_payables`) — shared across all tenants
   - `Organization` — id, name, slug, db_name, settings (JSONB), plan
   - `User` — email, full_name, hashed_password, sso_provider/id, organization_id
   - `Role` — name (admin, ap_manager, ap_clerk, cfo)
   - `UserRole` — junction table
   - `ExtractionUsage` — billing: invoice_id, provider, program_type, period
   - `CardRebate` — virtual_card_id, amount, rate, status, period

2. **Tenant DBs** (`ap_<slug>`) — isolated per customer
   - `Invoice` — invoice_number, vendor_name, amount, status (12 states), file_key, warnings (JSONB)
   - `InvoiceLineItem` — invoice_id, item_code, description, quantity, unit_price, total, gl_account
   - `InvoiceExtractionResult` — invoice_id, method, confidence, raw_result (JSONB)
   - `Vendor` — name, code, tax_id, status (active/unverified/inactive/rejected), source (manual/erp_sync/ai_extracted)
   - `PurchaseOrder` — po_number, vendor_id, total, status
   - `POLineItem` — po_id, description, quantity, unit_price, total
   - `GoodsReceipt` — gr_number, po_id, received_date, status
   - `GRLineItem` — gr_id, description, quantity_received
   - `GLAccount` — code, name, account_type, parent_code, erp_account_id
   - `PaymentRun` — status, total_amount, initiated_by, executed_at
   - `PaymentSchedule` — invoice_id, due_date, discount_date, discount_percent
   - `Payment` — invoice_id, payment_run_id, amount, method (ach/wire/check/virtual_card), status
   - `VirtualCard` — invoice_id, card_provider (lithic/nium), provider_card_id, amount_limit, status
   - `WorkflowDefinition` — name, steps_config (JSONB), is_active, is_default
   - `WorkflowInstance` — definition_id, invoice_id, current_step, state, steps_config_snapshot (JSONB)
   - `WorkflowStep` — instance_id, step_number, step_type, assigned_to, action, completed_at
   - `AuditLog` — actor_id, action, entity_type, entity_id, details (JSONB)
   - `Exception` — invoice_id, exception_type, severity, status (open/resolved/escalated/dismissed)

**Connection management** (`database.py`):
- `get_control_db()` → AsyncSession for control plane
- `get_tenant_db()` → AsyncSession for tenant (via `X-Tenant-Slug` header)
- Engine pool: `pool_size=5, max_overflow=10` per tenant; `pool_size=10, max_overflow=20` for control
- All engines disposed on app shutdown

## Invoice workflow state machine

```python
VALID_TRANSITIONS = {
    new:              {pending, ready_for_review, done},
    pending:          {ready_for_review, failed},
    ready_for_review: {approved, rejected},
    approved:         {sending_to_erp, done},
    rejected:         {ready_for_review, new},
    sending_to_erp:   {sent_to_erp, failed},
    sent_to_erp:      {done},
    done:             {},  # terminal
    failed:           {pending, sending_to_erp},
}
```

Step types: `extraction` → `approval` → `erp_export` → `done`

`workflow_engine.py` functions: `validate_transition()`, `transition_invoice()`, `get_invoice_for_update()` (SELECT...FOR UPDATE), `create_workflow_instance()`, `advance_workflow()`, `is_step_enabled()`.

**Snapshot pattern**: `WorkflowInstance.steps_config_snapshot` freezes the active definition at invoice creation. All runtime logic reads the snapshot, not the live definition.

## Adapter patterns

### Extraction adapters (`services/extraction_adapters/`)

```python
@register_extraction_adapter("my_provider")
class MyAdapter(ExtractionAdapter):
    async def extract(self, file_bytes, file_key, mime_type, file_url) -> ExtractionResult: ...
    async def test_connection(self) -> bool: ...
```

Registered: `claude_vision`, `openai_vision`, `aws_textract`, `ollama`, `mock`

`ExtractionResult` contains per-field `ExtractedField(value, confidence)` + `line_items` + `overall_confidence`.

**Two program types**: `platform` (app-level Claude Vision key, usage tracked) vs `byok` (customer provides own API key).

### ERP adapters (`services/erp_adapters/`)

```python
@register_adapter("my_erp")
class MyErpAdapter(ErpAdapter):
    async def post_invoice(self, payload: InvoicePayload) -> ErpPostResult: ...
    async def get_invoice_status(self, erp_document_id) -> ErpInvoiceStatus: ...
    async def void_invoice(self, erp_document_id) -> bool: ...
    async def test_connection(self) -> bool: ...
```

Registered: `merge_dev`, `dynamics_365_bc`, `netsuite`, `mock`

Config `integration_method: "merge_dev"|"direct"` selects whether to use Merge.dev unified API or direct adapter.

ERP send has retry logic: up to 3 attempts with exponential backoff (2s, 4s, 8s).

### Card adapters (`services/card_adapters/`)

Registered: `lithic`, `nium`, `mock`. Both have sandbox modes.

## Dispatch modes

Extraction, ERP push, and audit logging support two execution modes:
- **local** (default) — runs in background thread with separate DB engine
- **lambda** — sends message to SQS, processed by Lambda handler

Files: `*_dispatch.py` (router), `*_lambda.py` (Lambda handler).

## Authentication (`api/deps.py`)

- JWT HS256 signed with `AP_SECRET_KEY`, 30-min expiry (configurable)
- Token payload: `sub` (user_id), `org` (org_id), `jti` (unique ID for blocklist)
- `get_current_user()` — FastAPI dependency, returns User or 401
- Logout adds `jti` to Redis blocklist with TTL matching token expiry

## Organization settings (JSONB)

Stored in `Organization.settings`:
```json
{
  "company": { "name", "tax_id", "address", "phone", "website", "logo_url" },
  "invoice_defaults": { "currency", "payment_terms", "number_prefix", "default_gl_account", "default_cost_center" },
  "erp": { "type", "integration_method", "credentials": { ... } },
  "extraction": { "program_type": "platform"|"byok", "provider", "api_key", "model" },
  "cards": { "program_type": "platform"|"byok", "provider", ... }
}
```

## Exception types

`duplicate`, `po_mismatch`, `fraud_flag`, `extraction_failed`, `unverified_vendor`, `review_rejected`, `amount_exceeded`, `missing_data`

Severity: `error`, `warning`, `info`. Auto-detected by `invoice_warnings.py`.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/seed.py` | Creates 2 tenants (acme, techflow) with full sample data (vendors, invoices, POs, payments, exceptions) |
| `scripts/create_tenant.py` | CLI wrapper around `services.tenant_provisioning.provision_tenant` — provisions a single tenant (org + admin user + DB + tables) |
| `scripts/migrate_all_tenants.py` | Runs `alembic upgrade head` on every tenant DB |

## Self-service tenant signup

Two-step flow under `/api/signup`:

| Endpoint | Purpose |
|----------|---------|
| `POST /api/signup/start` | Rate-limit check, captcha verify, slug-format check, slug-availability check. Creates an `EmailVerification` row (24h TTL) and sends a verification email. No tenant resources created yet. |
| `GET /api/signup/slug-check?slug=…` | Cheap inline availability check for the signup form. |
| `POST /api/signup/complete` | Consumes the token, re-checks slug availability, provisions the tenant via `services.tenant_provisioning.provision_tenant`, generates a temp password, sends the welcome email with tenant URL + credentials, marks the verification consumed. |
| `POST /api/auth/change-password` | Authenticated. Validates current password, enforces complexity, sets the new hash and clears `User.must_change_password`. |

The welcome email contains the tenant URL (`AP_TENANT_URL_TEMPLATE`, e.g. `https://{slug}.app.com`) and a 16-char URL-safe temp password. The user is forced to change it on first login (`User.must_change_password` is `true` until they hit `/api/auth/change-password`).

**Pluggable services:**

- `services/email_adapters/` — `console` (local dev, logs to stdout) and `ses` (AWS SES) via `AP_EMAIL_PROVIDER`. Same registry pattern as extraction/ERP adapters.
- `services/tenant_provisioning.py` — reusable async `provision_tenant()` used by both the CLI and the API.
- `services/rate_limit.py` — Redis sliding-window limiter. Signup: `AP_SIGNUP_RATE_LIMIT_PER_HOUR` (default 5).
- `utils/slug.py` — regex + reserved-word blocklist + DB uniqueness check.
- `utils/hcaptcha.py` — server-side siteverify. Skips when `AP_HCAPTCHA_SECRET` is empty (local dev).
- `utils/passwords.py` — `generate_temp_password()` + `validate_password_complexity()` (min 12 chars, upper/lower/digit).

The captcha sitekey is exposed to the frontend via `GET /api/public-config` so the SvelteKit build doesn't need to bake it in.

Relevant env vars: `AP_EMAIL_PROVIDER`, `AP_EMAIL_FROM`, `AP_AWS_SES_REGION`, `AP_PUBLIC_URL`, `AP_TENANT_URL_TEMPLATE`, `AP_HCAPTCHA_SECRET`, `AP_HCAPTCHA_SITEKEY`, `AP_SIGNUP_RATE_LIMIT_PER_HOUR`.

## Secrets management (SOPS + AWS KMS)

Deployed-environment secrets are encrypted at rest with [SOPS](https://github.com/getsops/sops) backed by an AWS KMS key. Local dev still uses plain `backend/.env` (copied from `backend/.env.example`), which is gitignored.

**File layout:**

```
backend/
├── .env                 # local dev — gitignored, copy from .env.example
├── .env.example         # local dev template — committed
└── .env.sops            # deployed secrets, AWS KMS-encrypted — committed

infra/
├── terraform.tfvars.example   # committed template
└── terraform.tfvars.sops      # encrypted TF vars — committed
```

**One-time bootstrap per clone** (creates the KMS key, populates `.sops.yaml`, seeds the `.sops` files):

```bash
brew install sops awscli jq    # or apt/yum equivalent
aws configure                   # must be authenticated with an IAM principal
                                # that has kms:CreateKey + kms:CreateAlias
./bin/sops-init.sh
```

The script is idempotent; re-runs reuse the existing KMS key.

**Edit an encrypted file:**

```bash
sops backend/.env.sops             # decrypts → $EDITOR → re-encrypts on save
sops infra/terraform.tfvars.sops
```

**Decrypt to a plaintext .env (e.g. to run the deployed backend locally against prod-like config):**

```bash
sops -d backend/.env.sops > backend/.env
```

`backend/.env` is gitignored, so the plaintext copy stays on your laptop.

**Load into a container entrypoint:**

```bash
set -a
. <(sops -d backend/.env.sops)
set +a
exec python main.py
```

**Adding a collaborator:** grant them `kms:Decrypt` (and usually `kms:Encrypt`, `kms:GenerateDataKey`) on the project's KMS key via an IAM policy. No changes to `.sops.yaml` and no re-encryption needed — IAM is the source of truth.

**Rotating the KMS key:** run `aws kms update-alias` to point the alias at a new key, then `sops updatekeys backend/.env.sops` (and the same for tfvars) to re-encrypt under the new key material.

See `infra/README.md` and the comments at the top of `.sops.yaml` for full context.

## Conventions

- **Async only** — all DB operations use SQLAlchemy 2 async. Don't introduce sync DB calls.
- **ruff** — `ruff check .` and `ruff format .` before committing. Line length 100.
- **Schemas** — Pydantic v2 models in `app/schemas/` for all request/response types.
- **No dotenv in Lambda paths** — `main.py` imports dotenv for local dev; Lambda entry points must not.
- **Tenant isolation** — always resolve tenant via dependency injection (`get_tenant_db()`), never hardcode DB names.
- **Row locking** — use `get_invoice_for_update()` for any status transition to prevent race conditions.
