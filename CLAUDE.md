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

The repo root has a `package.json` with `pnpm` dispatch scripts that wrap each workspace's native toolchain — `pnpm run` lists them. The native commands still work, and CI calls them directly.

```bash
# Common tasks via root pnpm scripts (any working directory)
pnpm install:all              # bootstrap all three workspaces
pnpm db:up                    # core services: Postgres + Redis + MinIO (docker compose up -d)
pnpm idp:up                   # local IdPs (opt-in `idp` profile): Keycloak (OIDC SSO, :8088) + Authentik (SCIM, :9002)
pnpm idp:seed                 # point the acme tenant's settings.sso at local Keycloak (OIDC SSO)
pnpm saml:seed                # point acme's settings.sso at local Keycloak via SAML (protocol=saml; replaces OIDC block)
pnpm test:saml                # SAML SSO e2e (tests-e2e/saml/) — real Keycloak handshake
pnpm scim:seed                # set acme's SCIM bearer token to match the Authentik blueprint (SCIM)
pnpm test:scim                # SCIM provisioning e2e (tests-e2e/scim/)
pnpm aws:up                   # local AWS emulator (LocalStack :4566, opt-in `aws` profile): SQS/SES/CloudWatch/S3-ObjectLock
pnpm ollama:up                # local AI model server (Ollama :11435, opt-in `ai` profile) for the ollama extraction adapter
pnpm stripe:up                # Stripe API mock (stripe-mock :12111, opt-in `payments` profile) for the stripe_treasury adapter
pnpm mail:up                  # Mailpit SMTP sink + web inbox (:1025/:8025, opt-in `mail` profile) for the smtp email adapter
pnpm services:up              # everything at once: core + IdPs + LocalStack + Ollama + stripe-mock + Mailpit (services:down / services:logs / services:reset too)
pnpm seed                     # python scripts/seed.py
pnpm dev                      # backend (:8000) + frontend (:7777) together, one Ctrl-C stops both
pnpm dev:all                  # db:up (core only), then pnpm dev (whole web stack from cold)
pnpm dev:full                 # services:up (core + every opt-in profile), then pnpm dev — the entire stack from cold
pnpm dev:backend              # python main.py (loads backend/.env.development, then .env override)
pnpm dev:frontend             # vite dev on :7777
pnpm dev:mobile               # flutter run (needs a device/emulator — not part of `pnpm dev`)
pnpm lint                     # ruff + svelte-check + flutter analyze
pnpm test                     # pytest + Playwright + flutter test
pnpm migrate:all              # alembic upgrade head + migrate_all_tenants.py

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

The backend dispatch scripts (`lint:backend`, `test:backend`, `format:backend`, `dev:backend`, `seed`, `migrate*`) assume the backend venv is activated — `source backend/.venv/bin/activate` before invoking them, or call the native commands from inside an already-activated shell.

## First-time setup

1. `cd backend && docker compose up -d`
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -e ".[dev]"`
4. `python scripts/seed.py` — creates 2 demo tenants with sample data
5. `cd ../frontend && pnpm i`
6. Open http://acme.localhost:7777 — login: `demo@acme.com` / `demo`

No `.env` setup at all: `backend/.env.development` and
`frontend/.env.development` are **committed** with safe, no-risk local defaults
(loopback URLs, mock adapters, the `change-me` JWT key, MinIO's
minioadmin/minioadmin), so a fresh clone runs immediately. The backend loads
them via `main.py` (local-dev entrypoint only); the frontend loads
`.env.development` natively in Vite dev mode. Personal overrides go in a
gitignored `backend/.env` / `frontend/.env.local` and win over the committed
defaults. Deployed secrets stay in the `*.sops` files — never in any `.env*`.

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
| `/auth/saml` | SAML 2.0 SSO — config (public), login (302 AuthnRequest), acs (verify + JIT + mint), exchange (one-time-code → JWT), metadata. SP-initiated; reuses the OIDC JIT/session tail |
| `/scim/v2` | SCIM 2.0 user provisioning from Okta/Entra/Authentik — list/get/create/PUT/PATCH/delete (per-tenant bearer auth) |
| `/portal/auth` | Supplier-portal auth (VendorUser, JWT `typ=vendor`) — login, logout, me, change-password |
| `/portal` | Supplier-portal endpoints — invoice submit/list + payment history, PO flip, remittance download, company/bank/tax self-service (bank/tax stage for AP approval), vendor-scoped |
| `/admin` | User CRUD, role assignment |
| `/organization` | Org settings, ERP/extraction connection tests, SCIM token mint |
| `/invoices` | Invoice CRUD, bulk ops, upload, extraction, approve/reject, ERP send, audit-log summary (`GET/POST {id}/summary`) |
| `/vendors` | Vendor CRUD, ERP sync |
| `/payments` | Payment listing, payment runs (create/execute) |
| `/cards` | Virtual card issuance (Lithic/Nium), webhooks, rebates |
| `/purchase-orders` | PO listing, ERP sync |
| `/goods-receipts` | Goods-receipt list / detail (3-way match feeder) |
| `/gl-accounts` | GL account CRUD, ERP sync |
| `/credit-memos` | Credit-memo CRUD, vendor application |
| `/tax` | 1099 tracking (W-9 upload, YTD totals, Tax1099 export) |
| `/analytics` | CFO dashboard aggregates + CSV/PDF exports + scheduled-report CRUD |
| `/assistant` | Conversational AP assistant — chat over 5 fixed read-only tools (current tenant only), conversation history, token-usage meter. Mock adapter default (local-first); claude adapter when keyed |
| `/workflows` | Workflow definition CRUD, active steps |
| `/adaptive` | Approval-pattern learning, baseline anomalies, advisory workflow suggestions (read-only + dismiss) |
| `/audit` | SOX auditor export — per-invoice / date-range trail (JSON+CSV, admin/CFO, GET-only); itself audited |
| `/exceptions` | Exception queue, resolution; autonomous AI agents — `agent-resolve` (run an agent on one exception), `agent-decisions` (decision log), `agent-stats` (resolution/escalation rates) |
| `/notifications` | Per-user in-app notification center (list, unread-count, mark-read, read-all) + email/in-app preferences |
| `/dashboard` | KPI aggregates (pipeline, aging, spend, trends) |
| `/erp` | Inbound ERP webhooks (status updates) |
| `/email-intake` | Inbound email webhook (provider-signed) — turns attachments into invoices |
| `/organization/email-intake` | Admin — show / rotate the per-tenant intake address |
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
| `audit_summary.py` | One-paragraph LLM/template summary of an invoice's audit timeline; cached on `invoices.meta`, keyed to an audit-log fingerprint. Fail-soft to a deterministic template (local-dev default). See `backend/docs/audit-summary.md`. |
| `audit_log_shipper.py` | Background loop that ships tenant `audit_log` rows to CloudWatch Logs + S3 Object Lock (SOC 2 centralized WORM store) |
| `adaptive_workflows.py` | Deterministic per-vendor/per-approver approval stats + baseline anomaly + advisory suggestion derivation (pure, no LLM). See `backend/docs/adaptive-workflows.md`. |

### Adapter patterns (pluggable providers)

- **Extraction** (`services/extraction_adapters/`): claude_vision, openai_vision, aws_textract, ollama, mock. Registry via `@register_extraction_adapter` decorator.
- **ERP** (`services/erp_adapters/`): merge_dev (unified), dynamics_365_bc, netsuite, mock. Registry via `@register_adapter` decorator. Config `integration_method: "merge_dev"|"direct"` selects path.
- **Cards** (`services/card_adapters/`): lithic, nium, mock. Both have sandbox modes.
- **Payments** (`services/payment_adapters/`): modern_treasury, stripe_treasury, increase, column, dwolla (ACH only), checkeeper (check printing), mock. Webhook-driven status; HMAC-verified signatures; tenant in webhook URL path.
- **Audit shipping** (`services/audit_shipping/`): mock, cloudwatch, s3_objectlock. Registry via `@register_audit_shipping_adapter` decorator. Sinks for the centralized SOC 2 audit trail; list configured via `AP_AUDIT_SHIPPING_PROVIDERS`.
- **FX rates** (`services/fx_adapters/`): mock, openexchangerates. Locked once per international payment at submission and persisted on the row. See `backend/docs/international-payments.md`.
- **Sanctions / KYC** (`services/sanctions_adapters/`): mock, complyadvantage. Called by `services/compliance.check_payment_compliance` before every payment-adapter call.
- **Email (outbound)** (`services/email_adapters/`): console (dev default), smtp (Mailpit / any relay), ses. Selects via `AP_EMAIL_PROVIDER`. Used by signup + welcome flows.
- **Email intake (inbound)** (`services/email_intake_adapters/`): ses, mailgun, generic. Parses provider-specific inbound webhook payloads into a normalised `InboundEmail`.
- **Embeddings** (`services/embedding_adapters/`): mock (dev default), openai. Powers RAG + duplicate-similarity search.

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
**Tenant-scoped**: Entity, Invoice, InvoiceLineItem, InvoiceExtractionResult, Vendor, VendorChangeRequest, PurchaseOrder, POLineItem, GoodsReceipt, GRLineItem, GLAccount, PaymentRun, PaymentSchedule, Payment, VirtualCard, WorkflowDefinition, WorkflowInstance, WorkflowStep, AuditLog, Exception, AgentDecision, Notification

**Multi-entity**: business tables (Invoice, Vendor, PurchaseOrder, GoodsReceipt, Payment, PaymentRun, CreditMemo, Exception, GLAccount, WorkflowDefinition, VirtualCard) carry a nullable `entity_id` FK (`EntityMixin`) to the tenant-local `Entity` (subsidiary). Every tenant has one `is_default` Entity; rows backfill to it (GLAccount stays NULL = shared chart). Phase 2 scopes reads/writes by the `X-Entity-ID` header (`app/tenant.py` → `get_entity_id` / `get_write_entity_id` / `apply_entity_scope`) with a sidebar entity switcher; CFO analytics + per-entity workflow selection are deferred (2b / 3). See `docs/multi-entity.md`.

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
| `AP_AUDIT_SUMMARY_ENABLED` | `true` | Master switch for the invoice audit-log summary. When `false`, `GET /api/invoices/{id}/summary` returns the deterministic template summary with no LLM call. Reuses the extraction key/model — no new secret. |
| `AP_AUDIT_SUMMARY_MODEL` | (empty) | Model for the audit summary; falls back to `AP_EXTRACTION_MODEL` when empty. |
| `AP_REDIS_URL` | `redis://localhost:6379` | Token blocklist |
| `AP_LITHIC_API_KEY` | (empty) | Lithic virtual cards |
| `AP_NIUM_CLIENT_*` | (empty) | Nium virtual cards |
| `AP_MFA_ENABLED` | `false` | Master MFA switch — keep `false` in local dev, flip on in deployed envs |
| `AP_API_PUBLIC_URL` | `http://localhost:8000` | Externally-reachable backend base URL. Builds the SAML SP entityId + ACS URL the IdP POSTs to (unlike OIDC's frontend redirect). Set to the real API host in deployed envs |
| `AP_SAML_ACS_PATH` | `/login/saml-callback` | Frontend SPA bridge route the SAML ACS 303-redirects to (with a one-time handoff code) |
| `AP_SAML_SP_PRIVATE_KEY` / `AP_SAML_SP_CERT` | (empty) | Optional SP signing keypair — only when an IdP requires SP-signed AuthnRequests. Real secret → sops; empty by default (local Keycloak runs with SP signing off) |
| `AP_HSTS_ENABLED` | `false` | Emit `Strict-Transport-Security` on every response — keep `false` in local HTTP dev, flip on in deployed envs |
| `AP_AUDIT_SHIPPING_ENABLED` | `false` | Master switch for the centralized audit-log shipper — keep `false` in local dev, flip on in deployed envs |
| `AP_AUDIT_SHIPPING_PROVIDERS` | `mock` | Comma-separated adapter names (e.g. `cloudwatch,s3_objectlock`). All must succeed before rows are marked shipped. |
| `AP_AUDIT_SHIPPING_S3_BUCKET` | (empty) | Object-Lock-enabled S3 bucket for the WORM copy; required when the `s3_objectlock` provider is enabled |
| `AP_AUDIT_SHIPPING_CLOUDWATCH_GROUP` | `/ap/audit` | CloudWatch Logs group for shipped audit events |
| `AP_AUDIT_MODE` | `local` | `local` or `lambda` — same shape as `AP_EXTRACTION_MODE` |
| `AP_EMAIL_INTAKE_DOMAIN` | (empty) | Hostname for inbound intake addresses (`invoices+<token>@<domain>`). Empty disables email intake. |
| `AP_EMAIL_INTAKE_SIGNING_SECRET` | (empty) | HMAC-SHA256 signing secret for the email-intake webhook body. Required whenever `AP_EMAIL_INTAKE_DOMAIN` is set — boot refuses otherwise. |
| `AP_MAX_CONCURRENT_SESSIONS` | `5` | Max concurrent sessions per user. Oldest JTI is evicted onto the blocklist when exceeded. `0` disables the cap. |
| `AP_NOTIFICATIONS_ENABLED` | `true` | Master switch for email + in-app notifications. When `false`, the `transition_invoice` / `assign_reviewer` hooks skip dispatch. Dispatch is always best-effort regardless (a failure never breaks a transition). See `backend/docs/notifications.md`. |
| `AP_REPORTING_CURRENCY_DEFAULT` | `USD` | Platform last-resort reporting (base) currency for multi-currency rollups when an org sets no `reporting_currency`. Per-org override on `Organization.settings.reporting_currency`. See `backend/docs/multi-currency.md`. |

Full list in `backend/app/config.py`.

## Where to look

| Topic | Read this |
|-------|-----------|
| Frontend details | `frontend/CLAUDE.md` — routes, stores, components, API mappings |
| Backend details | `backend/CLAUDE.md` + `backend/docs/` — models, services, adapters, migrations |
| Mobile app | `mobile/CLAUDE.md` — Flutter iOS app, screens, stores, API client |
| AI extraction | `backend/docs/ai-extraction.md` — platform vs BYOK, provider configs |
| Conversational assistant | `backend/docs/conversational-assistant.md` — fixed toolset, mock/claude adapters, token budget, audit |
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
| Local SSO + SCIM testing | `docs/local-sso-keycloak.md` — Keycloak (OIDC SSO) + Authentik (SCIM) via Docker; `pnpm idp:up`, `idp:seed`, `scim:seed`, `test:scim` |
| Local SAML SSO testing | `docs/local-sso-saml.md` — SAML via the same Keycloak; `pnpm idp:up`, `saml:seed`, `test:saml` |
| Local AWS testing | `docs/local-aws-localstack.md` — LocalStack via Docker for SQS/SES/CloudWatch/S3-ObjectLock; `pnpm aws:up`, `AP_AWS_ENDPOINT_URL` |
| Local email preview | `docs/local-email-mailpit.md` — Mailpit via Docker for the `smtp` email adapter; `pnpm mail:up`, `AP_EMAIL_PROVIDER=smtp` |
| Multi-tenancy | `docs/multi-tenancy.md` — DB isolation, provisioning |
| Multi-entity | `docs/multi-entity.md` — subsidiaries within a tenant (`entity_id`, Default entity, Phase 1) |
| Architecture | `docs/architecture.md` — system overview |
| Environment vars | `docs/environment.md` — frontend + backend config |
| Deployment | `docs/production-deployment.md` — AWS, CloudFront, ALB, ECS |
| SOC 2 readiness | `docs/soc2-readiness.md` — vendor comparison, control mapping, kickoff plan |
| Founder runbooks (non-code) | `docs/founder-runbooks/` — legal, prod deploy, Stripe, payment rails, SOC 2 vendor, support + status |
| CSV data import | `backend/docs/csv-import.md` — pilot Day-0 vendor + invoice migration |
| Email-to-invoice intake | `backend/docs/email-intake.md` — per-tenant inbound address, SES + Mailgun setup |
| 1099 tracking | `backend/docs/tax-1099.md` — W-9 collection, YTD reporting, Tax1099 integration sketch |
| Audit-log shipping | `backend/docs/audit-log-shipping.md` — centralized WORM sink, adapters, S3 Object Lock caveats |
| Notifications | `backend/docs/notifications.md` — email + in-app events, the `transition_invoice` hook, recipient matrix, preferences |
| Exception agents | `backend/docs/exception-agents.md` — autonomous exception resolution, autonomy thresholds, `AgentDecision` log, amount-mismatch resolver |
| Adaptive AI workflows | `backend/docs/adaptive-workflows.md` — approval-pattern learning, baseline anomaly read, advisory suggestions (advisory-only, no LLM) |
| Data enrichment | `backend/docs/data-enrichment.md` — auto-fill (GL/cost-center/terms), line-item price variance, vendor performance scoring from supplier history (advisory / compute-on-read, no external calls) |
| Backup + DR | `docs/backup-disaster-recovery.md` — RTO/RPO, restore procedures, test cadence |
| Secrets rotation | `docs/secrets-rotation.md` — what to rotate, when, and how |
| Getting started | `docs/getting-started.md` — first-run setup |
| Troubleshooting | `docs/troubleshooting.md` — common issues |
| Self-service signup | `docs/self-service-signup.md` — signup flow, email adapters, abuse mitigations |
| Supplier portal | `backend/docs/supplier-portal.md` — VendorUser auth, invoice submission, phase 2 deferrals |
| Roadmap | `docs/roadmap.md` — feature backlog with status and competitive context |
| Competition | `docs/competitive-analysis.md` — competitor matrix, gaps, advantages |

Prefer reading docs over guessing. Update them when behavior changes.

## Guard rails

Standing rules for how to work in this repo. They are not optional; when in
doubt, follow the rule and say so. (Several are detailed in their own sections
below or in `## Project invariants` — this is the index.)

1. **Commit each piece of work; never push.** One logical unit = one
   path-scoped commit, as you finish it. See [Git workflow](#git-workflow).
2. **Add test coverage with the change.** A behavior change ships with tests in
   the same session (pytest / Playwright / flutter). If something is genuinely
   untestable, say *why* rather than skipping silently. See [Every change must
   update docs and tests](#every-change-must-update-docs-and-tests).
3. **Code-review important code.** For non-trivial or load-bearing changes
   (auth, tenant isolation, migrations, the money/payment path, webhook
   handlers, PII), run a review pass before committing — `/check`, `/safe-edit`,
   or the `code-reviewer` agent. Don't gate trivial edits (typos, comments, dep
   bumps) on it.
4. **Never code around an issue — fix the root cause.** No masking: no inflated
   timeouts, sleeps, retries, skipped/loosened assertions, or swallowed errors.
   See [Fix bugs at the source](#fix-bugs-at-the-source--never-adjust-the-test-to-hide-them).
5. **Always recommend the long-term solution.** When a quick patch and a durable
   fix diverge, lead with the durable one and name the trade-off — don't let an
   expedient workaround pass as the answer silently.
6. **No dangling "deferred" / "out-of-scope" findings.** A real issue you
   surface — in a review, an audit, a `/bug-hunt` or `/audit-and-fix` report, a
   code comment, or your own analysis — must be driven to a concrete resolution,
   not left as a passing mention. Default: fix it the same session when it's
   bounded and you've already diagnosed it. Defer only when a fix is genuinely
   too large or risky to land now — and a deferral needs a tracked follow-up
   (issue / ticket — confirm before creating) naming what's broken, the durable
   fix, and the trigger to do it. "Deferred / recommended" in a report is a
   staging area, not a destination. Extends rails 4–5: surfacing an issue is the
   start of the obligation, not the end of it.
7. **Local-first.** Every part of the app must run on a dev laptop with no cloud
   account. Each external dependency ships with a local equivalent *and* a safe
   local default that points at it: Postgres/Redis/MinIO via Compose; provider
   integrations default to their `mock` adapter (extraction, ERP, cards,
   payments, FX, sanctions, audit shipping); email defaults to `console`; SSO is
   off by default with **Keycloak** (`pnpm idp:up`) as the local IdP. When you
   add a dependency on an external service, add its local equivalent and a safe
   local default in the *same* change — never make `pnpm dev` require a real
   SaaS credential.
8. **A pnpm script per service.** Every service or long-running process a
   contributor starts in dev gets a root `package.json` script — don't make
   anyone memorize raw `docker compose --profile …` invocations (`pnpm db:up`,
   `pnpm idp:up`, `pnpm services:up`). Add the script in the same change you add
   the service.
9. **Reusable components.** Build UI from the shared component libraries —
   `frontend/src/lib/components/` (Svelte 5 runes only: `$state` / `$derived` /
   `$effect` / `$props`) and the mobile widget library — instead of copy-pasting
   markup across routes/screens; extract a component the second time you'd
   duplicate it. See [frontend/CLAUDE.md](frontend/CLAUDE.md) and
   [mobile/CLAUDE.md](mobile/CLAUDE.md).
10. **Organize files by responsibility.** Put code in the file / dir its
    siblings already establish (backend: `app/api/`, `app/services/` + the
    adapter subdirs, `app/models/`, `app/schemas/`; frontend:
    `src/lib/{stores,utils,components}` + `api.ts`). Don't dump unrelated logic
    into a file just because it's open — extend the file that owns that
    responsibility. Read the per-area `CLAUDE.md` before editing.
11. **Honour the project invariants.** Money is `Decimal`/`Numeric`; writes that
    move money are idempotent; status changes write audit rows; tenant isolation
    is enforced at the data layer; auth before everything; secrets via sops+KMS;
    PII/banking data stays out of logs; migrations fan out to every tenant;
    webhooks verify signatures + dedupe. Full enumeration with severities in
    [Project invariants](#project-invariants).
12. **Docs-as-code.** A behaviour, command, env var, port, or convention change
    updates its docs in the same turn — deferred docs are drift. See [Every
    change must update docs and tests](#every-change-must-update-docs-and-tests).

## Every change must update docs and tests

1. **Update tests** — add or adjust coverage for behavior you touched. No tests exist yet — create them when adding new features.
2. **Update docs** — if the change affects architecture, commands, env vars, deployment, or features, update the relevant doc (and this CLAUDE.md if setup or workflows changed).

## Git workflow

- **Commit each piece of work; never push.** Land every logical unit of work as its own path-scoped commit (`git commit -m "…" -- path/to/file …`) as you finish it — don't leave the tree dirty across tasks or batch unrelated changes into one commit. **Never `git push`** in this repo; publishing is the operator's call.
- Path-scoped commits are also required by the `.claude/hooks/git-scope-guard.py` PreToolUse hook — bare `git commit`, `git add -A/.`, `git commit -a`, and whole-tree ops are blocked. If a git command is denied, follow the scoped alternative in its message.
- No `Co-Authored-By` / "Generated with" trailer in commits or PRs — write them as a human would.

### Running concurrent sessions — use a worktree

When more than one Claude (or person) works this repo at once, **start each
session in its own git worktree** so they never share a working tree:

```bash
claude --worktree <name>      # e.g. claude --worktree clickable-rows
```

Each lands in `.claude/worktrees/<name>/` — a full checkout on its own branch,
gitignored, branched from local `HEAD` (`worktree.baseRef: "head"` in
`.claude/settings.json`, because this repo runs ahead of an unpushed `origin`).

Why it matters: the scope-guard is **path-granular, not hunk-granular**. In a
*shared* checkout it stops a session from committing files it didn't name, but
it can't separate two sessions' edits to the **same file** — a path-scoped
commit of that file captures whatever is in the one shared working tree. A
worktree removes the shared tree entirely, which is the only real fix. (`isolation: "worktree"` on a subagent isolates that subagent, not the top-level session.)

Worktree notes:
- The committed `*.env.development` defaults travel with the worktree, so the
  stack runs immediately. Gitignored personal env overrides are copied via
  `.worktreeinclude`.
- `backend/.venv` and `node_modules` do **not** carry over (venv paths are
  absolute; node_modules is heavy) — run `pnpm install` / recreate the venv in
  the worktree before building or testing there.
- **All work must end up on `main`.** A worktree commits on its own branch, and
  git won't let a worktree check out `main`, so that work only reaches `main`
  via an explicit merge from the **primary checkout**. Before retiring a
  worktree, consolidate it:
  ```bash
  git branch --no-merged main          # audit: anything still off main?
  git merge <worktree-branch>          # from the primary checkout (on main)
  ```
  Use `git merge --ff-only` when main hasn't moved (linear); otherwise rebase
  the worktree branch onto `main` first to keep history linear. Claude prompts
  to keep or remove the worktree on exit (auto-removes it if you left no
  changes) — but removal does **not** merge; consolidate first.
- This is backed by a safety net: the `SessionStart` hook
  `.claude/hooks/unmerged-worktree-check.sh` warns at the start of every session
  if any branch holds commits not on `main`, so stranded worktree work surfaces
  and gets merged instead of forgotten.

## Fix bugs at the source — never adjust the test to hide them

When a test fails, the only acceptable resolution paths are:

1. **The test itself is broken** (wrong fixture, missing required field, typo, race in test setup, unique-constraint collision with seed data). Fix the test.
2. **The app has a real bug or missing primitive.** Fix the app code. If the app needs a new affordance for the test to wait deterministically (a `data-ready` attribute backed by a real readiness signal, an exposed status, a broadcast handshake), add it in the app code — it's a real API, not test scaffolding.

There is no third option. These are forbidden because they ship the bug behind a green check:

- Inflating a Playwright `expect` / `toBeVisible` timeout to absorb a flake (`5_000` → `15_000` → `30_000`). Fix whatever makes the page slow.
- `await page.waitForTimeout(N)` between two actions. Wait on a real signal (DOM node, state attribute, network response).
- Bumping `--retries` (or relying on Playwright's `retries: 1`) to mask a real race.
- `test.skip(…)` / `test.fixme(…)` / `test.fail(…)` (or pytest's `@pytest.mark.skip` / `xfail`) against a real bug without an open follow-up that names what's broken + when it'll be fixed.
- Loosening strict assertions (`assert x == 'foo'` → `assert 'foo' in x.lower() or 'bar' in x.lower()`) to "absorb variance" — the variance IS the bug.
- Replacing a real wait with a sleep "because the real signal is unreliable" — the real signal needs fixing.

If you spot a candidate fix that fits one of those patterns: stop, surface the underlying app issue, and either fix it in the same session or flag it explicitly. Don't half-mask it via the test.

## Conventions and gotchas

- **Static frontend** — no SSR. All dynamic data goes through the backend API.
- **Svelte 5 runes** — `$state`, `$derived`, `$effect`, `$props` — not the legacy options API.
- **API client** — all frontend fetches go through `frontend/src/lib/api.ts` (auto-adds JWT + tenant header).
- **Python style** — ruff for lint/format. Line length 100. Python 3.12+ features allowed.
- **Migrations** — Alembic for all schema changes. Must run on every tenant DB, not just control plane.
- **Secrets** — never commit a secret-bearing `.env`. The only committed env files are `*.env.development` (safe local-dev defaults only) and the KMS-encrypted `*.sops` files.
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
- **Secrets via sops + AWS KMS, no hardcoded fallback.** Long-lived secrets live only in `*.sops` files, decrypted via the project's KMS key. A new `os.environ["X"]` with a fallback like `or "some-default"` for a secret is `Critical`. The only committed env files are `*.env.development` (safe local-dev defaults only — loopback URLs, mock adapters, the `change-me` JWT key) and the encrypted `*.sops` files; a committed `.env` / `.env.local` / `.env.production` carrying a real secret is `Critical`.
- **PII / banking data stays out of logs and error responses.** Bank account numbers, tax IDs, full vendor addresses, and full payment-method numbers must not appear in `logger` output, in HTTP error bodies, or in URL query strings. A `print` / `logger.info(...)` containing one of those fields is `Critical`.
- **Migrations are idempotent and run on every tenant DB.** New Alembic revisions use safe DDL (`IF NOT EXISTS` / `IF EXISTS` where applicable). A schema change that lands as control-plane-only when the change should fan out to every tenant is `Critical` — see `Don't modify tenant DBs outside of Alembic migrations` in `## What not to do`.
- **Webhook handlers verify signatures and dedupe by event id.** A new handler that doesn't verify the provider's HMAC, or doesn't dedupe by `event.id`, is `Critical` — webhook providers retry on any non-2xx and dedup is the only thing keeping a one-time effect one-time. The shared helpers live in `backend/app/services/webhook_security.py` (`verify_hmac_sha256`, `is_event_already_processed`, `extract_signature_header`); every webhook also returns 204 silently on every rejection path so the response doesn't enumerate.
- **Passwords use the shared `bcrypt_sha256` context.** `backend/app/utils/passwords.py::pwd_context` is the single hash context across the codebase; it uses `bcrypt_sha256` to side-step bcrypt's 72-byte truncation. A new `CryptContext(schemes=["bcrypt"], ...)` instantiation anywhere is `Critical` — see `.claude/hooks/security-patterns.sh` rule `bcrypt-truncation`.
