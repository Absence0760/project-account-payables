# CLAUDE.md

Guidance for Claude Code working in this repository. Keep this file short — it loads into every conversation.

## Project

Full-stack accounts payable management app. SvelteKit frontend + FastAPI backend with multi-tenant (database-per-tenant) architecture. Features: invoice extraction (AI/OCR), workflow engine, ERP integration, payment runs, virtual cards, exception tracking.

## Stack

- **frontend/** — SvelteKit 2, Svelte 5 (runes), adapter-static, TypeScript, pnpm. Dev port `7777`.
- **backend/** — FastAPI, Python 3.12+, SQLAlchemy 2 async, Alembic, PostgreSQL 16, Redis 7, MinIO (S3). Dev port `8000`.
- **mobile/** — Flutter 3.41+, Dart 3.11+, iOS + Android. Material 3, ChangeNotifier stores.
- **infra/** — Terraform skeleton for future AWS resources; SOPS-encrypted tfvars. See `infra/README.md`.
- **Local infra** — Docker Compose for Postgres/Redis/MinIO. GitHub Pages for frontend deploy.
- **Secrets** — `backend/.env.sops` + `infra/terraform.tfvars.sops`, both AWS KMS-encrypted via SOPS. Bootstrap with `./bin/sops-init.sh`. See `backend/CLAUDE.md` → Secrets management.

## Commands

```bash
# Frontend (from frontend/)
pnpm i                       # install
pnpm dev                     # dev server on :7777
pnpm build                   # production build
pnpm check                   # typecheck

# Backend (from backend/)
docker compose up -d          # start Postgres, Redis, MinIO
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"       # install with dev deps
python scripts/seed.py        # seed demo data
python main.py                # dev server on :8000 (auto-reload)

# Backend testing & linting
pytest                        # run tests
ruff check .                  # lint
ruff format .                 # format

# Mobile (from mobile/)
flutter pub get               # install
flutter run                   # run on iOS simulator
flutter analyze               # lint
flutter test                  # run tests

# Database migrations (from backend/)
alembic revision --autogenerate -m "description"   # create migration
alembic upgrade head                                # apply to control plane
AP_MIGRATE_TENANT=ap_acme alembic upgrade head      # apply to one tenant
python scripts/migrate_all_tenants.py               # apply to all tenants
```

## First-time setup

1. `cd backend && docker compose up -d`
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -e ".[dev]"`
4. `python scripts/seed.py` — creates 2 demo tenants with sample data
5. `cd ../frontend && pnpm i && cp .env.example .env`
6. Open http://acme.localhost:7777 — login: `demo@acme.com` / `demo`

## Multi-tenancy

- **Control plane DB** (`account_payables`): organizations, users, roles
- **Tenant DBs** (`ap_<slug>`): invoices, vendors, payments, workflows, etc.
- Frontend extracts subdomain → sends `X-Tenant-Slug` header → backend resolves tenant DB
- Provision: `python scripts/create_tenant.py --name "Corp" --slug corp --admin-email admin@corp.com --admin-password changeme`

## Architecture overview

### Backend routers (all under `/api`)

| Prefix | Purpose |
|--------|---------|
| `/auth` | Login, logout, profile (JWT + Redis blocklist), MFA enroll/verify/disable, MFA challenge |
| `/auth/sso` | OIDC SSO — config (public), authorize (302 to IdP), callback (JIT-provision + mint JWT) |
| `/scim/v2` | SCIM 2.0 user provisioning from Okta/Entra (per-tenant bearer auth) |
| `/portal/auth` | Supplier-portal auth (VendorUser, JWT `typ=vendor`) — login, logout, me, change-password |
| `/portal` | Supplier-portal endpoints — invoice submit/list + payment history, vendor-scoped |
| `/admin` | User CRUD, role assignment |
| `/organization` | Org settings, ERP/extraction connection tests, SCIM token mint |
| `/invoices` | Invoice CRUD, bulk ops, upload, extraction, approve/reject, ERP send |
| `/vendors` | Vendor CRUD, ERP sync |
| `/payments` | Payment listing, payment runs (create/execute) |
| `/cards` | Virtual card issuance (Lithic/Nium), webhooks, rebates |
| `/purchase-orders` | PO listing, ERP sync |
| `/gl-accounts` | GL account CRUD, ERP sync |
| `/workflows` | Workflow definition CRUD, active steps |
| `/exceptions` | Exception queue, resolution |
| `/dashboard` | KPI aggregates (pipeline, aging, spend, trends) |
| `/erp` | Inbound ERP webhooks (status updates) |
| `/signup` | Self-service tenant signup (start / slug-check / complete) |
| `/health` | Health check |

### Key services (`backend/app/services/`)

| Service | What it does |
|---------|-------------|
| `workflow_engine.py` | Invoice state machine with valid transitions, step orchestration |
| `extraction.py` | Dispatches AI extraction (platform Claude Vision or BYOK provider) |
| `erp.py` | Pushes approved invoices to ERP with retry logic |
| `review.py` | Approve/reject with field corrections |
| `po_matching.py` | 2-way/3-way invoice-to-PO matching — invoked by `invoice_warnings.refresh_warnings` after every extraction and on every invoice mutation; result persisted on `Invoice.po_match` |
| `vendor_matching.py` | Fuzzy vendor matching by name/code/tax_id |
| `invoice_warnings.py` | Generates warnings and exceptions (duplicates, fraud, etc.) |
| `payment_erp_sync.py` | Syncs payment status back to ERP |
| `storage.py` | S3/MinIO file upload/download |
| `audit_log_shipper.py` | Background loop that ships tenant `audit_log` rows to CloudWatch Logs + S3 Object Lock (SOC 2 centralized WORM store) |

### Adapter patterns (pluggable providers)

- **Extraction** (`services/extraction_adapters/`): claude_vision, openai_vision, aws_textract, ollama, mock. Registry via `@register_extraction_adapter` decorator.
- **ERP** (`services/erp_adapters/`): merge_dev (unified), dynamics_365_bc, netsuite, mock. Registry via `@register_adapter` decorator. Config `integration_method: "merge_dev"|"direct"` selects path.
- **Cards** (`services/card_adapters/`): lithic, nium, mock. Both have sandbox modes.
- **Payments** (`services/payment_adapters/`): modern_treasury, mock. Webhook-driven status; HMAC-verified signatures; tenant in webhook URL path.
- **Audit shipping** (`services/audit_shipping/`): mock, cloudwatch, s3_objectlock. Registry via `@register_audit_shipping_adapter` decorator. Sinks for the centralized SOC 2 audit trail; list configured via `AP_AUDIT_SHIPPING_PROVIDERS`.

To add a new adapter: copy `mock_adapter.py`, implement the interface, register with the decorator.

### Dispatch modes

Extraction, ERP, and audit operations support two execution modes via config:
- `local` (default) — jobs queued in-process; pool of 3 worker threads drains the queue (engines use `pool_size=1, max_overflow=0` to stay under PostgreSQL's connection limit)
- `lambda` — sends to SQS, processed by Lambda worker

Controlled by: `AP_EXTRACTION_MODE`, `AP_ERP_MODE`, `AP_AUDIT_MODE`

### Invoice workflow state machine

```
new → pending → ready_for_review → approved → sending_to_erp → sent_to_erp → posted_in_erp → payment_scheduled → paid → done
                      ↕ rejected ↔ new                                   ↘                                  ↑
                                    failed → pending | sending_to_erp     approved ----------------- (direct schedule, no ERP)
                                                                          ↑ (void) ←──────────────────────────────┘
```

Terminal state: `done`. Step types: `extraction`, `approval`, `erp_export`, `done`.
Workflow definitions are snapshotted per-invoice — editing a definition does not affect in-flight invoices.
The void-payment path (`POST /api/payments/{id}/void`) takes `payment_scheduled` or `paid` back to `approved` so the invoice re-enters the queue. Authoritative graph: `backend/app/services/workflow_engine.py::VALID_TRANSITIONS`.

### Data models

**Control plane**: Organization, User, Role, UserRole, ExtractionUsage, CardRebate
**Tenant-scoped**: Invoice, InvoiceLineItem, InvoiceExtractionResult, Vendor, PurchaseOrder, POLineItem, GoodsReceipt, GRLineItem, GLAccount, PaymentRun, PaymentSchedule, Payment, VirtualCard, WorkflowDefinition, WorkflowInstance, WorkflowStep, AuditLog, Exception

### RBAC roles

`admin`, `ap_manager`, `ap_clerk`, `cfo` — checked in both backend (deps.py) and frontend (auth store).

## Key environment variables (`AP_` prefix)

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_DATABASE_URL` | `postgresql+asyncpg://...localhost:5432/account_payables` | Control plane DB |
| `AP_SECRET_KEY` | `change-me-in-production` | JWT signing (HS256) |
| `AP_S3_ENDPOINT_URL` | `http://localhost:9000` | MinIO/S3 |
| `AP_EXTRACTION_MODE` | `local` | `local` or `lambda` |
| `AP_ERP_MODE` | `local` | `local` or `lambda` |
| `AP_ANTHROPIC_API_KEY` | (empty) | Claude Vision for platform extraction |
| `AP_EXTRACTION_MODEL` | `claude-sonnet-4-20250514` | AI model for extraction |
| `AP_EXTRACTION_AUTO_ROTATE` | `true` | Run Tesseract OSD on rendered PDF pages before sending to vision adapters. No-ops if `pytesseract` / `tesseract` missing. |
| `AP_REDIS_URL` | `redis://localhost:6379` | Token blocklist |
| `AP_LITHIC_API_KEY` | (empty) | Lithic virtual cards |
| `AP_NIUM_CLIENT_*` | (empty) | Nium virtual cards |
| `AP_MFA_ENABLED` | `false` | Master MFA switch — keep `false` in local dev, flip on in deployed envs |
| `AP_HSTS_ENABLED` | `false` | Emit `Strict-Transport-Security` on every response — keep `false` in local HTTP dev, flip on in deployed envs |
| `AP_AUDIT_SHIPPING_ENABLED` | `false` | Master switch for the centralized audit-log shipper — keep `false` in local dev, flip on in deployed envs |
| `AP_AUDIT_SHIPPING_PROVIDERS` | `mock` | Comma-separated adapter names (e.g. `cloudwatch,s3_objectlock`). All must succeed before rows are marked shipped. |
| `AP_AUDIT_SHIPPING_S3_BUCKET` | (empty) | Object-Lock-enabled S3 bucket for the WORM copy; required when the `s3_objectlock` provider is enabled |
| `AP_AUDIT_SHIPPING_CLOUDWATCH_GROUP` | `/ap/audit` | CloudWatch Logs group for shipped audit events |
| `AP_MAX_CONCURRENT_SESSIONS` | `5` | Max concurrent sessions per user. Oldest JTI is evicted onto the blocklist when exceeded. `0` disables the cap. |

Full list in `backend/app/config.py`.

## Where to look

| Topic | Read this |
|-------|-----------|
| Frontend details | `frontend/CLAUDE.md` — routes, stores, components, API mappings |
| Backend details | `backend/CLAUDE.md` + `backend/docs/` — models, services, adapters, migrations |
| Mobile app | `mobile/CLAUDE.md` — Flutter iOS app, screens, stores, API client |
| AI extraction | `backend/docs/ai-extraction.md` — platform vs BYOK, provider configs |
| ERP integration | `backend/docs/erp-integration.md` — adapter pattern, Merge.dev, direct APIs |
| Workflow design | `backend/docs/workflow-design.md` — state machine, step types, snapshots |
| Payments | `backend/docs/payments.md` — payment runs, schedules, ERP sync |
| Virtual cards | `backend/docs/virtual-cards.md` — Lithic/Nium, rebates, webhooks |
| PO matching | `backend/docs/po-matching.md` — 2-way/3-way matching logic |
| Vendor mgmt | `backend/docs/vendor-management.md` — sources, sync, matching |
| Local AI testing | `backend/docs/local-ai-testing.md` — Ollama setup |
| API reference | `backend/docs/api-reference.md` — REST endpoints |
| DB / Redis / MinIO | `backend/docs/{database,redis,minio,docker}.md` — backend infra |
| Auth & RBAC | `docs/authentication.md`, `docs/user-management.md` |
| Multi-tenancy | `docs/multi-tenancy.md` — DB isolation, provisioning |
| Architecture | `docs/architecture.md` — system overview |
| Environment vars | `docs/environment.md` — frontend + backend config |
| Deployment | `docs/production-deployment.md` — AWS, CloudFront, ALB, ECS |
| SOC 2 readiness | `docs/soc2-readiness.md` — vendor comparison, control mapping, kickoff plan |
| Founder runbooks (non-code) | `docs/founder-runbooks/` — legal, prod deploy, Stripe, payment rails, SOC 2 vendor, support + status |
| CSV data import | `backend/docs/csv-import.md` — pilot Day-0 vendor + invoice migration |
| Email-to-invoice intake | `backend/docs/email-intake.md` — per-tenant inbound address, SES + Mailgun setup |
| 1099 tracking | `backend/docs/tax-1099.md` — W-9 collection, YTD reporting, Tax1099 integration sketch |
| Audit-log shipping | `backend/docs/audit-log-shipping.md` — centralized WORM sink, adapters, S3 Object Lock caveats |
| Backup + DR | `docs/backup-disaster-recovery.md` — RTO/RPO, restore procedures, test cadence |
| Secrets rotation | `docs/secrets-rotation.md` — what to rotate, when, and how |
| Getting started | `docs/getting-started.md` — first-run setup |
| Troubleshooting | `docs/troubleshooting.md` — common issues |
| Self-service signup | `docs/self-service-signup.md` — signup flow, email adapters, abuse mitigations |
| Supplier portal | `backend/docs/supplier-portal.md` — VendorUser auth, invoice submission, phase 2 deferrals |
| Roadmap | `docs/roadmap.md` — feature backlog with status and competitive context |
| Competition | `docs/competitive-analysis.md` — competitor matrix, gaps, advantages |

Prefer reading docs over guessing. Update them when behavior changes.

## Every change must update docs and tests

1. **Update tests** — add or adjust coverage for behavior you touched. No tests exist yet — create them when adding new features.
2. **Update docs** — if the change affects architecture, commands, env vars, deployment, or features, update the relevant doc (and this CLAUDE.md if setup or workflows changed).

## Conventions and gotchas

- **Static frontend** — no SSR. All dynamic data goes through the backend API.
- **Svelte 5 runes** — `$state`, `$derived`, `$effect`, `$props` — not the legacy options API.
- **API client** — all frontend fetches go through `frontend/src/lib/api.ts` (auto-adds JWT + tenant header).
- **Python style** — ruff for lint/format. Line length 100. Python 3.12+ features allowed.
- **Migrations** — Alembic for all schema changes. Must run on every tenant DB, not just control plane.
- **Secrets** — never commit `.env` files. `.env.example` files are safe templates.
- **Two backend entry points** — `main.py` for local dev (auto-reload), `app/main.py:app` for production (uvicorn).
- **Async everywhere** — all DB operations use SQLAlchemy 2 async. Don't mix sync/async.
- **Workflow snapshots** — `WorkflowInstance.steps_config_snapshot` is frozen at invoice creation. Read the snapshot, not the live definition, for in-flight invoices.
- **Redis** — used for JWT token blocklist (logout), not general caching.

## What not to do

- Don't add a test framework other than pytest (backend) or vitest (frontend).
- Don't replace pnpm with npm/yarn.
- Don't add SSR adapters to the frontend; it must stay static for GitHub Pages.
- Don't call secret-bearing services from the frontend — go through the backend.
- Don't modify tenant DBs outside of Alembic migrations.
- Don't add `dotenv` imports to modules reachable from Lambda entry points.
- Don't hardcode tenant DB names — always use `ap_<slug>` via config.

## Project invariants

These are the rules the `.claude/agents/code-reviewer.md` agent cites. A diff that violates one is `Critical` unless the project explicitly opts out in writing. Stack-specific enforcement notes are in parentheses.

- **Money is exact.** Amounts use `Decimal` (never `float`), and SQLAlchemy columns for currency use `Numeric(precision, scale)` (never `Float` / `Real`). A new column or in-memory total typed as `float` for currency is `Critical`.
- **Idempotency on writes that move money.** Anything that initiates a payment, reverses a payment, or confirms an invoice as paid must be idempotent at the API boundary. The mechanism is whichever the backend already uses (idempotency-key header, request-id table, or a DB-level unique constraint on the operation tuple). A new "send payment" / "post payment" / "confirm payable" handler with no idempotency story is `Critical`.
- **Audit trail is append-only.** Status transitions on invoices, payments, approvals, and vendors write a log row through the audit-shipping infrastructure (`services/audit_shipping/` — see `## Architecture overview`), not just mutate state. A status change that overwrites without producing an audit row is `Improvement` at minimum, `Critical` if the field is regulated (`paid_at`, `approved_at`, `void_at`).
- **Tenant isolation is enforced at the data layer, not just by application code.** Every read / write resolves the tenant DB via the `X-Tenant-Slug` header → `ap_<slug>` mapping (see `## Multi-tenancy`). `backend/app/tenant.py::get_tenant` is the chokepoint and cross-checks the JWT's `org` claim against the resolved tenant — so a leaked / spoofed header alone can't widen access. A new query that runs against the control-plane DB while reading tenant data, hardcodes a tenant DB name, or constructs a tenant engine outside `get_tenant_db` is `Critical`.
- **Auth before everything.** Every route under `/api` is behind the auth middleware unless it is documented public-by-design. A new route mounted before the auth dependency, or one that references the user's identity without the auth dependency injected, is `Critical`. Approval / payment endpoints also check role / RBAC, not just authentication.
- **Secrets via sops + AWS KMS, no hardcoded fallback.** Long-lived secrets live only in `*.sops` files, decrypted via the project's KMS key. A new `os.environ["X"]` with a fallback like `or "some-default"` for a secret is `Critical`. No committed `.env` files (`.env.example` templates are fine).
- **PII / banking data stays out of logs and error responses.** Bank account numbers, tax IDs, full vendor addresses, and full payment-method numbers must not appear in `logger` output, in HTTP error bodies, or in URL query strings. A `print` / `logger.info(...)` containing one of those fields is `Critical`.
- **Migrations are idempotent and run on every tenant DB.** New Alembic revisions use safe DDL (`IF NOT EXISTS` / `IF EXISTS` where applicable). A schema change that lands as control-plane-only when the change should fan out to every tenant is `Critical` — see `Don't modify tenant DBs outside of Alembic migrations` in `## What not to do`.
- **Webhook handlers verify signatures and dedupe by event id.** A new handler that doesn't verify the provider's HMAC, or doesn't dedupe by `event.id`, is `Critical` — webhook providers retry on any non-2xx and dedup is the only thing keeping a one-time effect one-time. The shared helpers live in `backend/app/services/webhook_security.py` (`verify_hmac_sha256`, `is_event_already_processed`, `extract_signature_header`); every webhook also returns 204 silently on every rejection path so the response doesn't enumerate.
- **Passwords use the shared `bcrypt_sha256` context.** `backend/app/utils/passwords.py::pwd_context` is the single hash context across the codebase; it uses `bcrypt_sha256` to side-step bcrypt's 72-byte truncation. A new `CryptContext(schemes=["bcrypt"], ...)` instantiation anywhere is `Critical` — see `.claude/hooks/security-patterns.sh` rule `bcrypt-truncation`.
