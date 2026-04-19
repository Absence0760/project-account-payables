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

### Adapter patterns (pluggable providers)

- **Extraction** (`services/extraction_adapters/`): claude_vision, openai_vision, aws_textract, ollama, mock. Registry via `@register_extraction_adapter` decorator.
- **ERP** (`services/erp_adapters/`): merge_dev (unified), dynamics_365_bc, netsuite, mock. Registry via `@register_adapter` decorator. Config `integration_method: "merge_dev"|"direct"` selects path.
- **Cards** (`services/card_adapters/`): lithic, nium, mock. Both have sandbox modes.

To add a new adapter: copy `mock_adapter.py`, implement the interface, register with the decorator.

### Dispatch modes

Extraction, ERP, and audit operations support two execution modes via config:
- `local` (default) — runs in background thread in-process
- `lambda` — sends to SQS, processed by Lambda worker

Controlled by: `AP_EXTRACTION_MODE`, `AP_ERP_MODE`, `AP_AUDIT_MODE`

### Invoice workflow state machine

```
new → pending → ready_for_review → approved → sending_to_erp → sent_to_erp → done
                      ↕ rejected ↔ new
                                    failed → pending | sending_to_erp
```

Terminal state: `done`. Step types: `extraction`, `approval`, `erp_export`, `done`.
Workflow definitions are snapshotted per-invoice — editing a definition does not affect in-flight invoices.

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
| `AP_REDIS_URL` | `redis://localhost:6379` | Token blocklist |
| `AP_LITHIC_API_KEY` | (empty) | Lithic virtual cards |
| `AP_NIUM_CLIENT_*` | (empty) | Nium virtual cards |
| `AP_MFA_ENABLED` | `false` | Master MFA switch — keep `false` in local dev, flip on in deployed envs |

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
| Getting started | `docs/getting-started.md` — first-run setup |
| Troubleshooting | `docs/troubleshooting.md` — common issues |
| Self-service signup | `docs/self-service-signup.md` — signup flow, email adapters, abuse mitigations |
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
