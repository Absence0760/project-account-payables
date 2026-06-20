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
| `/portal` | Supplier-portal endpoints — invoice submit/list + payment history, PO flip, remittance download, UBL 2.1 e-invoice download (`GET /portal/invoices/{id}/einvoice`, vendor-scoped), company/bank/tax self-service (bank/tax stage for AP approval), W-9/W-8 tax-form self-service (`GET/POST /portal/company/tax-form`, `GET .../tax-form/file` — reuses `Vendor.w9_file_key`/`w9_received_date`, applies live, PII-free audit, no migration), early-payment discount offers (`GET /portal/discount-offers`, `POST .../{id}/accept`\|`/decline` — reuses the dynamic-discounting engine; accept flips status only, never moves money; idempotent), vendor notification preferences (`GET/PATCH /portal/notification-preferences` — vendor-controlled email-on-paid/rejected, wired into the `transition_invoice` chokepoint; migration 0052), supplier chat (`GET/POST /portal/invoices/{id}/chat`, `POST .../chat/attachments`, `GET .../chat/file/{key}` — vendor-scoped, AP author ids masked, no resolve/mention/template), single-use virtual-card reveal (`GET /portal/cards/{token}`), MFA/TOTP (`POST /portal/auth/mfa/{enroll,verify,disable,challenge}` — opt-in, `AP_MFA_ENABLED`-gated, distinct `typ=vendor_mfa_challenge`; migration 0053), vendor-scoped |
| `/admin` | User CRUD, role assignment |
| `/organization` | Org settings, ERP/extraction connection tests, SCIM token mint; white-label branding (`GET/PUT /organization/branding` — per-tenant product name, logo URL, accent/accent-strong hex colors, support/legal URLs on `settings.brand`; GET readable by any authed user, admin-only mutate, audited `organization.branding_updated` PII-free, validated hex colors + http(s) URLs; settings-JSON, no migration; frontend themes via CSS custom properties — see `docs/white-label.md`); data residency (`GET/PUT /organization/data-residency` — GDPR/CCPA region pin `us`/`eu`/`uk`/`ca`/`au` on `settings.residency.region`; admin-only mutate, audited `organization.residency_updated`; settings-JSON, no migration; see `docs/data-residency.md`) |
| `/invoices` | Invoice CRUD, bulk ops, upload, extraction, approve/reject, ERP send, audit-log summary (`GET/POST {id}/summary`), UBL 2.1 e-invoice export (`GET {id}/einvoice?format=ubl`, role-gated, 422 on tax-invalid), PEPPOL AS4 transmission (`POST {id}/peppol-send`, role-gated, idempotent), contract link (`POST {id}/link-contract` \| `/unlink-contract`, spend attribution), inter-company routing (`POST {id}/route-intercompany`, admin/ap_manager — generates the mirror payable under the counterparty entity; idempotent on `intercompany_mirror_id`; audited on both rows; see `backend/docs/inter-company.md`), supplier chat (`GET/POST {id}/chat`, `POST {id}/chat/attachments`, `POST {id}/chat/{resolve,reopen}` role-gated, `GET chat/templates`, `GET chat/file/{key}` cross-tenant-checked), **email approval** (`GET /invoices/email-action/{token}` confirm page + `POST .../confirm` — PUBLIC, signed single-action token IS the credential; approve/reject the assigned invoice from the notification email with no login, via the normal review path; `email_actions.py` / `services/email_action_token.py`; see `backend/docs/email-approval.md`) |
| `/vendors` | Vendor CRUD, ERP sync; sanctions screening (`POST {id}/screen`, `GET {id}/screening-history`, `GET screening/review-queue`, `POST {id}/block`\|`/unblock` — screen also runs on create/update), vendor risk (`GET {id}/risk`, `POST {id}/risk/recompute`, `GET risk/summary`). See `backend/docs/vendor-risk-screening.md` |
| `/vendor-statements` | Vendor statement reconciliation — reconcile a supplier's statement of open items against our AP ledger. `POST` (manual lines) / `POST /upload` (CSV) create a run + classified `VendorStatementReconLine`s (matched / amount_mismatch / missing_on_our_side / missing_on_their_side) via the pure `services/vendor_statement_recon.py`; `GET` list, `GET {id}` detail+lines, `POST {id}/lines/{line_id}/resolve` (resolve/ignore), `DELETE {id}`, `GET close-readiness` (period-close gate — vendors with a material unreconciled balance). Read all four roles; mutate admin/ap_manager; entity-scoped; every mutation audited. See `backend/docs/vendor-statement-reconciliation.md` |
| `/positive-pay` | Positive Pay / payment-fraud files — hand the bank the cheques we *issued* (`check_issue`, per payment run) or the accounts authorized to debit us (`ach_authorization`, org-wide) so it refuses anything that doesn't match. `POST /payment-runs/{run_id}/check-issue` (idempotent on `(run, bank_format)` via `uq_positive_pay_run_format`) / `POST /ach-authorization` render via a pluggable per-bank formatter (`services/positive_pay_adapters/`: csv \| fixed_width) → store the file in MinIO → persist a PII-free `PositivePayFile` row; `GET` list, `GET {id}` detail, `GET {id}/download` (cross-tenant-checked), `POST {id}/process-return` (classify the bank's presented items → deduped `fraud_flag` Exceptions: altered cheques map to their invoice, never-issued cheques become standalone invoice-less ones — migration 0049 made `Exception.invoice_id` nullable), `DELETE {id}`. Read admin/ap_manager/cfo; mutate admin/ap_manager (treasury control — clerks excluded); entity-scoped; every mutation audited. Full account/routing numbers live only in the MinIO file (DB stores `account_last4`). See `backend/docs/positive-pay.md` |
| `/payments` | Payment listing, payment runs (create/execute) |
| `/discounts` | Dynamic discounting — supplier-offered sliding-scale early-pay offers (`GET/POST /offers`, `POST {id}/accept`\|`/decline`), per-invoice ROI (`GET /invoices/{id}/roi`), cash-constrained optimizer (`POST /optimize`), bulk vendor negotiation (`POST /bulk-negotiate`), captured/missed/projected-savings dashboard (`GET /dashboard`). Read all four roles; mutate admin/ap_manager (accept also cfo); every mutation audited; entity-scoped. See `backend/docs/dynamic-discounting.md` |
| `/recurring` | Recurring / subscription invoice templates — list (status/vendor_id/search/page filters), CRUD (DELETE 409s once invoices generated), `POST {id}/pause`\|`/resume`\|`/end`\|`/generate-now`, `GET {id}/upcoming-schedule?count=`, `GET {id}/history`. The `recurring_invoices` sweep generates the next pre-coded Invoice into the approval queue, idempotent on `(template, period_key)` via `uq_invoice_recurring_period`; never moves money. Read all four roles; mutate admin/ap_manager; entity-scoped; every mutation audited (`recurring_template.created/updated/paused/resumed/ended/deleted/generated`). See `backend/docs/recurring-invoices.md` |
| `/cards` | Virtual card issuance (Lithic/Nium), webhooks, rebates |
| `/contracts` | Contract lifecycle (CLM) — CRUD + search/filter, document upload (`POST {id}/upload`) + proxy (`GET /file/{file_key}`, cross-tenant-checked), lifecycle (`POST {id}/activate\|terminate\|cancel\|renew`), spend summary on detail, contract-based PO creation (`POST {id}/create-po`). Read admin/ap_manager/ap_clerk/cfo; mutate admin/ap_manager; every mutation audited |
| `/expenses` | Expense Management — expense CRUD (list paginated + entity-scoped, status/report filters), receipt upload (`POST {id}/receipt` → `upload_expense_receipt`) + cross-tenant-checked download proxy (`GET /receipt/{file_key}`), CSV export, bulk GL re-code. WF3 refreshes `Expense.policy_violations` (best-effort) on every create/PATCH/receipt write via `services/expense_policy.evaluate_expense`. Read admin/ap_manager/ap_clerk/cfo; mutate admin/ap_manager/ap_clerk; every mutation audited |
| `/expense-reports` | Expense report CRUD + attach/detach expenses (`POST {id}/expenses`, recomputes `total_amount`) + summary. WF3 report approval: `POST {id}/submit` (draft→submitted; 422 + violation list if a blocking policy violation), `POST {id}/approve` (submitted→approved; `check_segregation` 403 on self-approve, CFO gate above `settings.expense_approval.cfo_threshold` default 5000), `POST {id}/reject` (submitted→rejected, children back to draft). Same read RBAC; approve allows cfo; every transition audited |
| `/expense-policies` | WF3 — expense reimbursement policy CRUD (per-diem, mileage rate, category limit, receipt/pre-approval thresholds) consumed by the policy engine. Read all four roles; mutate admin/ap_manager; audited; entity-scoped |
| `/expense-preapprovals` | WF3 — spend pre-approval requests: list (filter status/requester), create (requester = caller, status pending), `POST {id}/approve` \| `/reject` (admin/ap_manager; `check_segregation` blocks deciding your own request). Audited |
| `/corporate-card-transactions` | WF4 — corporate-card import + reconciliation: list (paginated, entity-scoped; status/virtual-card/date filters), `POST /import-csv` (idempotent on `(org, external_txn_id)`, shared `import_batch`), `POST /sync-virtual-cards` (charged `VirtualCard` spend → feed, dedupe `vc:<provider_card_id>`), `GET {id}/match-suggestions` (amount-exact + ±5d window + merchant fuzz), `POST {id}/match` (both-sides FK + `payment_method`, 409 if matched) \| `/unmatch` \| `/ignore` \| `/create-expense`. Read all four roles; mutate admin/ap_manager (create-expense also ap_clerk); every mutation audited; PII = `card_last_four` only |
| `/corporate-card-transactions` | WF4 — corporate-card import + reconciliation: list (entity-scoped; `reconciliation_status`/`virtual_card_id`/date filters), `POST /import-csv` (idempotent on `external_txn_id`), `POST /sync-virtual-cards` (charged virtual cards → `vc:<provider_card_id>`), `GET {id}/match-suggestions` (amount-exact + ±5d date + merchant fuzz), `POST {id}/{match,unmatch,ignore,create-expense}` (both-sides FK link + `payment_method`; create-expense allows ap_clerk). Read all four roles; mutate admin/ap_manager; PII = `card_last_four` only; every mutation audited |
| `/purchase-orders` | PO listing, ERP sync |
| `/goods-receipts` | Goods-receipt list / detail (3-way match feeder) |
| `/gl-accounts` | GL account CRUD, ERP sync |
| `/credit-memos` | Credit-memo CRUD, vendor application |
| `/tax` | 1099 tracking (W-9 upload, YTD totals, Tax1099 export) |
| `/analytics` | CFO dashboard aggregates + CSV/PDF exports + scheduled-report CRUD + cross-entity consolidated reporting (`GET /analytics/by-entity` — per-entity rollup + `consolidated` cross-check; ignores `X-Entity-ID`) |
| `/assistant` | Conversational AP assistant — chat (`POST /chat`) + SSE streaming (`POST /chat/stream`, `tool`/`delta`/`done`/`error` events; 429 before the stream) over 5 fixed read-only tools (current tenant only), conversation history, token-usage meter. `ollama` adapter is the committed dev default (local tool-capable model, fails soft to `mock`); `claude` when keyed; `mock` deterministic fallback |
| `/workflows` | Workflow definition CRUD, active steps; no-code builder — templates (`GET /templates`, `POST /from-template`), version history (`GET/POST {id}/versions`, `POST {id}/restore/{version_id}`, `GET {id}/versions/diff`), simulation (`POST {id}/simulate`), import/export (`GET {id}/export`, `POST /import`). Builder step types (`condition`, `parallel`, `webhook`, `email`, `delay`) live in `steps_config` JSONB; PATCH auto-snapshots prior steps into a `WorkflowVersion` |
| `/adaptive` | Approval-pattern learning, baseline anomalies, advisory workflow suggestions (read-only + dismiss) |
| `/audit` | SOX auditor export — per-invoice / date-range trail (JSON+CSV, admin/CFO, GET-only); itself audited |
| `/exceptions` | Exception queue, resolution; autonomous AI agents — `agent-resolve` (run an agent on one exception), `agent-decisions` (decision log), `agent-stats` (resolution/escalation rates) |
| `/notifications` | Per-user in-app notification center (list, unread-count, mark-read, read-all) + email/in-app preferences |
| `/privacy` | GDPR/CCPA data-subject rights (admin-only) — DSAR export (`POST /privacy/dsar` → portable PII bundle across control + tenant DBs, audited `privacy.dsar_export`) + right-to-erasure (`POST /privacy/erasure` → irreversibly redacts PII while preserving the money trail + append-only `audit_log`; idempotent; audited `privacy.erasure`) + request history (`GET /privacy/requests`). Subject types: `user`/`vendor_user`/`vendor_contact`; `DataSubjectRequest` model + migration 0054 (PII-free). See `backend/docs/privacy.md` |
| `/dashboard` | KPI aggregates (pipeline, aging, spend, trends) |
| `/billing` | **Platform billing & metering** (the AP platform's OWN customer billing — control-plane, keyed by org, distinct from the customer AP money path) — `GET /billing/subscription` (admin/cfo): current `Plan` + `Subscription` status + usage-to-date for the period. Control-plane `Plan`/`Subscription` models + migration 0056; usage rollup off `extraction_usage`/`card_rebates` (`services/billing/usage_rollup.py`, Decimal-exact); pluggable billing adapters (`billing_adapters/`: `mock` default \| `stripe_billing` fail-closed skeleton); entitlement gating `require_entitlement`/`require_api_entitlement` (402 on a plan miss, composes with `require_roles`/`require_api_scope`). FIRST SLICE — real Stripe, dunning, plan-change/invoices UI deferred. See `backend/docs/billing.md` |
| `/erp` | Inbound ERP webhooks (status updates) |
| `/email-intake` | Inbound email webhook (provider-signed) — turns attachments into invoices |
| `/peppol` | Inbound PEPPOL AS4 receive webhook — `POST /peppol/inbound/{tenant_slug}` (public-by-design, HMAC-gated, tenant in path). Dedupes redeliveries by AS4 MessageId (`uq_peppol_message_id`), parses the UBL/CII, creates the Invoice + an inbound `PeppolTransmission`, hands to the einvoice extractor. Always 204 |
| `/approvals/slack` | **Slack interactive approval** — `POST /approvals/slack/interactivity` (PUBLIC, no JWT). Approve/reject an assigned invoice from the Block Kit buttons on the Slack approval message, no app login. Gated by the Slack request signature (HMAC over `v0:{timestamp}:{body}` with `AP_SLACK_SIGNING_SECRET`, ±5-min replay window) **and** the signed single-use action token in the button `value` (the email-approval `email_action_token` reused, bound to a `slack` channel + the intended approver); the decision runs the normal `services/review` path (segregation + CFO gate + thresholds + immutable audit row + approval signature). Opaque 200 ack on every path (success AND rejection) — never enumerates. `slack_approvals.py`; see `backend/docs/slack-approval.md`. Teams interactivity deferred |
| `/organization/email-intake` | Admin — show / rotate the per-tenant intake address |
| `/signup` | Self-service tenant signup (start / slug-check / complete) |
| `/v1` | **Public Developer API (programmatic)** — versioned read surface authenticated by a per-org API key (`X-API-Key: ap_live_…`), NOT the SPA JWT. The key resolves org → tenant DB via the same `get_tenant_engine` chokepoint (no `X-Tenant-Slug`; the key IS the tenant boundary). `GET /v1/invoices` (paginated, `status`/`page`/`page_size`), `GET /v1/invoices/{id}` — stable `V1Invoice` shape decoupled from the ORM (money as exact JSON string). Gated `require_api_scope("read")` + `require_api_entitlement("public_api")` (plan gate → 402 when the org's plan doesn't include the public API) + `get_api_key_db`; opaque 401 on any auth failure; `AP_PUBLIC_API_ENABLED` kill switch. **Published, versioned OpenAPI contract scoped to this surface only**: `GET /v1/openapi.json` (machine-readable spec — only `/api/v1` paths, the `X-API-Key` security scheme, the `V1Invoice` shape, `servers` + `info.version: v1`; internal SPA routes/schemas never leak in) + `GET /v1/docs` (Swagger UI) — both public-to-read but 404 when `AP_PUBLIC_API_ENABLED` is off; generated from live routes by `app/api/v1_openapi.py`; deprecation policy in the doc. See `backend/docs/public-api.md` |
| `/api-keys` | API-key management (admin, JWT + RBAC) — `POST` mint (returns the plaintext key EXACTLY once + metadata), `GET` list (prefix + metadata only, never hash/plaintext), `DELETE {id}` soft-revoke (idempotent). Keys stored as `sha256(full_key)` + indexed `key_prefix` (NOT bcrypt — high-entropy tokens need indexed lookup; constant-time compare), control-plane `ApiKey` model + migration 0055. Every mint/revoke writes a PII-free `api_key.created`/`api_key.revoked` audit row. See `backend/docs/public-api.md` |
| `/webhooks` | **Outbound webhook management** (admin, JWT + RBAC) — the push counterpart of `/api/v1`. `POST` create a subscription (target URL + subscribed event types; returns the HMAC signing secret EXACTLY once, like an API-key mint), `GET` list (metadata only — never the full secret), `PATCH {id}`, `DELETE {id}` (CASCADEs its deliveries), `GET /deliveries` (org-scoped, filter `status`/`subscription_id`, paginated), `POST /deliveries/{id}/redeliver` (re-enqueue a `failed`/`dead` delivery, 409 on an already-`delivered` one). Control-plane `WebhookSubscription`/`WebhookDelivery` models + migration 0057 (both in `CONTROL_TABLES`). Dispatch (`services/webhooks/`) signs the frozen payload, POSTs with bounded retries + exponential backoff → dead-letter, dedupes on `(subscription, event_id)`; emits `invoice.approved` + `payment.settled` from the `transition_invoice` chokepoint (best-effort, never breaks the transition). Payloads are PII-free (money as exact string). Every mutation audited. `AP_WEBHOOKS_ENABLED` kill switch. See `backend/docs/public-api.md` § Outbound webhooks |
| `/health` | Health check |

### Key services (`backend/app/services/`)

| Service | What it does |
|---------|-------------|
| `workflow_engine.py` | Invoice state machine with valid transitions, step orchestration |
| `extraction.py` | Dispatches AI extraction (platform Claude Vision or BYOK provider) |
| `erp.py` | Pushes approved invoices to ERP with retry logic |
| `review.py` | Approve/reject with field corrections |
| `po_matching.py` | 2-way/3-way/4-way invoice-to-PO matching — invoked by `invoice_warnings.refresh_warnings` after every extraction and on every invoice mutation; result persisted on `Invoice.po_match`. The 4-way leg adds a Quality Inspection gate (pass/fail/partial acceptance). `require_inspection` + amount `tolerance_pct` are resolved per-invoice by `matching_rules` |
| `matching_rules.py` | Pure resolver for the effective PO-match rule — `require_inspection` + `tolerance_pct` per-field from `settings.matching.vendor_rules[<vendor_id>]` → `commodity_rules[<gl_account>]` → org default → hardcoded default. Never raises. See `backend/docs/po-matching.md` § Per-vendor / per-commodity rules |
| `qms_sync.py` | QMS inspection sync — pulls `QMSInspectionRecord`s from the configured `qms_adapters` provider into `quality_inspections`, idempotent upsert on `(org, inspection_number)`, PII-free `quality_inspection.synced` audit. `run_qms_sync_loop` background sweep (mirrors `contract_renewal`); `POST /api/inspections/sync` for manual runs |
| `vendor_matching.py` | Fuzzy vendor matching by name/code/tax_id |
| `invoice_warnings.py` | Generates warnings and exceptions (duplicates, fraud, etc.) |
| `payment_erp_sync.py` | Syncs payment status back to ERP |
| `storage.py` | S3/MinIO file upload/download |
| `audit_summary.py` | One-paragraph LLM/template summary of an invoice's audit timeline; cached on `invoices.meta`, keyed to an audit-log fingerprint. Fail-soft to a deterministic template (local-dev default). See `backend/docs/audit-summary.md`. |
| `audit_log_shipper.py` | Background loop that ships tenant `audit_log` rows to CloudWatch Logs + S3 Object Lock (SOC 2 centralized WORM store) |
| `adaptive_workflows.py` | Deterministic per-vendor/per-approver approval stats + baseline anomaly + advisory suggestion derivation (pure, no LLM). See `backend/docs/adaptive-workflows.md`. |
| `supplier_chat.py` | Embedded per-invoice supplier chat — lazy thread create, static `CHAT_TEMPLATES`, the org `supplier_chat.enabled` flag read, and the notification (AP managers / @mentions) + direct supplier portal-link email helpers shared by the AP (`api/invoices.py`) and portal (`api/portal.py`) routes. See `backend/docs/supplier-chat.md`. |
| `discount_roi.py` | Pure annualized-return ROI primitive for early-pay discounts (cost-of-forgoing-discount APR; days accelerated = net due − discount deadline). Shared by the optimizer, the auto-capture sweep, and the per-invoice ROI endpoint. See `backend/docs/dynamic-discounting.md`. |
| `discount_offers.py` | `DiscountOffer` tier selection + savings math + lifecycle mutators (accept/decline/capture/expire) + bulk-vendor offer builder. Pure; never commits. |
| `discount_optimizer.py` | Ranks open offers by APR and greedily selects the highest-yield worthwhile ones within a cash budget (capture vs. cash preservation). Pure. |
| `discount_auto_trigger.py` | Background sweep that auto-accepts open offers clearing `AP_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD`. Mirrors `contract_renewal`; **only flags `offered → accepted`, never moves money** (CFO-gated payment run still funds). |
| `recurring_invoices.py` | Recurring / subscription invoice generation sweep. Mirrors `discount_auto_trigger` / `contract_renewal`; finds `active` `RecurringInvoiceTemplate`s whose `next_run_on` has arrived, generates the next pre-coded `Invoice` into the approval queue (period_key `YYYY-MM` / `YYYY-Qn` / `YYYY`), advances `next_run_on`, and writes a `recurring_template.generated` audit row. **Idempotent on `(template, period_key)`** via the partial unique index `uq_invoice_recurring_period` — **never moves money** (CFO-gated payment run still funds). Disabled by default (`AP_RECURRING_INVOICES_ENABLED`). See `backend/docs/recurring-invoices.md`. |

### Adapter patterns (pluggable providers)

- **Extraction** (`services/extraction_adapters/`): claude_vision, openai_vision, aws_textract, ollama, einvoice, mock. Registry via `@register_extraction_adapter` decorator. `einvoice` is auto-selected (not config-driven) when an ingested file is a structured e-invoice (UBL 2.1 / Factur-X / ZUGFeRD) — see `services/e_invoice/` + `backend/docs/e-invoicing.md`.
- **ERP** (`services/erp_adapters/`): merge_dev (unified), dynamics_365_bc, netsuite, mock. Registry via `@register_adapter` decorator. Config `integration_method: "merge_dev"|"direct"` selects path.
- **Cards** (`services/card_adapters/`): lithic, nium, mock. Both have sandbox modes.
- **Payments** (`services/payment_adapters/`): modern_treasury, stripe_treasury, increase, column, dwolla (ACH only), checkeeper (check printing), mock. Webhook-driven status; HMAC-verified signatures; tenant in webhook URL path.
- **Positive Pay formatters** (`services/positive_pay_adapters/`): csv (default), fixed_width. Registry via `@register_positive_pay_formatter` decorator; `get_positive_pay_formatter` defaults to `csv` and falls back to `csv` on an unknown key. Renders a payment run's cheques (`check_issue`) or the org's ACH-authorized accounts (`ach_authorization`) into a per-bank Positive Pay file. The rendered file (full account/routing numbers) is stored in MinIO via `storage.upload_positive_pay_file`; the DB row is PII-free. See `backend/docs/positive-pay.md`.
- **Audit shipping** (`services/audit_shipping/`): mock, cloudwatch, s3_objectlock. Registry via `@register_audit_shipping_adapter` decorator. Sinks for the centralized SOC 2 audit trail; list configured via `AP_AUDIT_SHIPPING_PROVIDERS`.
- **FX rates** (`services/fx_adapters/`): mock, openexchangerates. Locked once per international payment at submission and persisted on the row. See `backend/docs/international-payments.md`.
- **Supplier financing** (`services/financing_adapters/`): mock (local-first default — deterministic, no network), c2fo (skeleton — live key required, fail-closed). Registry via `@register_financing_adapter`. A supply-chain-finance marketplace funds a supplier's early payment (advance = face − fee); the buyer repays at the net due date. Selected per-org via `Organization.settings.financing.provider`. See `backend/docs/dynamic-discounting.md`.
- **QMS / quality inspections** (`services/qms_adapters/`): mock (local-first default — deterministic pass/fail/partial fixtures, no network/credential), generic (httpx skeleton — fails closed without a per-org `base_url` + `api_key`). Registry via `@register_qms_adapter`. `services/qms_sync` pulls inspection records into `quality_inspections` (idempotent upsert on `(org, inspection_number)`); selected per-org via `Organization.settings.qms.provider` → `AP_QMS_PROVIDER`. See `backend/docs/po-matching.md` § QMS integration.
- **Sanctions / KYC** (`services/sanctions_adapters/`): mock, complyadvantage, dowjones, refinitiv (the last three are skeletons — live key required, fail-closed without one). Called by `services/compliance.check_payment_compliance` before every payment-adapter call, and by `services/vendor_screening.screen_vendor_record` on vendor create/update, the `vendor_rescreen` periodic sweep, and manual re-screens. Adverse-media hits surface via `ScreeningResult.categories`. See `backend/docs/vendor-risk-screening.md`.
- **Email (outbound)** (`services/email_adapters/`): console (dev default), smtp (Mailpit / any relay), ses. Selects via `AP_EMAIL_PROVIDER`. Used by signup + welcome flows.
- **Chat notifications (outbound)** (`services/chat_notification_adapters/`): mock (dev default — no network/credential), slack (`{text, blocks}` incoming webhook), teams (`MessageCard` incoming webhook). Registry via `@register_chat_notification_adapter`. Selects via `AP_CHAT_NOTIFICATION_PROVIDER` (default `mock`) → per-org `Organization.settings.chat_notifications` (provider + webhook_url + per-event toggles). Wired into `notification_dispatch.notify_event` as a best-effort, per-event channel post for the four approval events (assigned/approved/rejected/paid); a chat-send failure never breaks the transition. Fails closed (no-op + PII-free warning) when no webhook URL is configured; message is PII-free (invoice number, vendor, amount+currency, status, deep link only). See `backend/docs/notifications.md` § Chat notifications.
- **Email intake (inbound)** (`services/email_intake_adapters/`): ses, mailgun, generic. Parses provider-specific inbound webhook payloads into a normalised `InboundEmail`.
- **Embeddings** (`services/embedding_adapters/`): mock (dev default), openai. Powers RAG + duplicate-similarity search.
- **PEPPOL AS4 (send + receive)** (`services/peppol_adapters/`): mock (in-process default — no network), as4_gateway (real — hosted Access Point HTTP API, key via sops/no fallback). Registry via `@register_peppol_adapter`. Outbound transmits the `e_invoice` UBL onto the PEPPOL network; SMP/SML resolution + send; idempotent at the DB layer (partial unique index on `peppol_transmissions`). Inbound receive: the same adapters implement `parse_inbound`; the C4 webhook at `POST /api/peppol/inbound/{tenant_slug}` is HMAC-gated and dedupes redeliveries via `uq_peppol_message_id`. See `backend/docs/peppol.md` § Inbound.
- **Billing** (`services/billing_adapters/`): mock (in-process, deterministic, no network/credential — local-first default), stripe_billing (skeleton — live key via sops, **fails closed** without it; the provider API calls are documented skeletons but `parse_webhook` is implemented end-to-end with HMAC verify). Registry via `@register_billing_adapter`; `get_billing_adapter()` defaults to `mock`. The AP platform's OWN customer billing (plans / subscriptions / metering — control-plane, keyed by org). Selected per-org via `Organization.settings.billing.provider` → `AP_BILLING_PROVIDER`. See `backend/docs/billing.md`.
- **Vendor enrichment** (`services/enrichment_adapters/`): mock (deterministic synthetic firmographics, no network/credential — local-first default), dun_bradstreet + clearbit (httpx skeletons — live key via per-org settings, **fail closed** `EnrichmentNotConfigured` without it; no hardcoded fallback). Registry via `@register_enrichment_adapter`; `get_enrichment_adapter()` defaults to `mock`. External vendor firmographics (legal name / address / industry+SIC / employee count / website / DUNS) for `POST /api/enrichment/vendors/{id}/enrich` — **advisory / suggestion-only, never overwrites the Vendor row**; raw `tax_id` masked to `***<last4>`. Selected per-org via `Organization.settings.enrichment.provider` → `AP_VENDOR_ENRICHMENT_PROVIDER`. See `backend/docs/data-enrichment.md` § External enrichment.

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

**Control plane**: Organization, User, Role, UserRole, ExtractionUsage, CardRebate, ApiKey, Plan, Subscription, WebhookSubscription, WebhookDelivery
**Tenant-scoped**: Entity, Invoice, InvoiceLineItem, InvoiceExtractionResult, Vendor, VendorChangeRequest, PurchaseOrder, POLineItem, GoodsReceipt, GRLineItem, QualityInspection, GLAccount, PaymentRun, PaymentSchedule, Payment, VirtualCard, WorkflowDefinition, WorkflowInstance, WorkflowStep, AuditLog, Exception, AgentDecision, Notification, Contract, ContractLineItem, SupplierChatThread, SupplierChatMessage, ExpenseReport, Expense, ExpensePolicy, CorporateCardTransaction, ExpensePreapproval, DiscountOffer, RecurringInvoiceTemplate, VendorStatementReconciliation, VendorStatementReconLine

**Multi-entity**: business tables (Invoice, Vendor, PurchaseOrder, GoodsReceipt, Payment, PaymentRun, CreditMemo, Exception, GLAccount, WorkflowDefinition, VirtualCard) carry a nullable `entity_id` FK (`EntityMixin`) to the tenant-local `Entity` (subsidiary). Every tenant has one `is_default` Entity; rows backfill to it (GLAccount stays NULL = shared chart). Phase 2 + 2b scope reads/writes (incl. the dashboard + CFO analytics) by the `X-Entity-ID` header (`app/tenant.py` → `get_entity_id` / `get_write_entity_id` / `apply_entity_scope`) with a sidebar entity switcher. Phase 3 wires the entity-level chart of accounts (shared NULL ∪ entity) into the AI extraction GL catalog + bulk-recode validation and selects the entity's own `WorkflowDefinition` (shared fallback; one default per `(org, entity)` via `uq_workflow_definitions_one_default`, migration 0050). Phase 4 adds inter-company invoice routing (`counterparty_entity_id` / `intercompany_mirror_id`, migration 0051) + cross-entity consolidated reporting (`GET /analytics/by-entity`). Multi-entity is **complete** (Phases 1–4). See `docs/multi-entity.md`.

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
| `AP_ASSISTANT_PROVIDER` | `mock` (code) / `ollama` (`.env.development`) | Conversational assistant adapter — `mock` \| `claude` \| `ollama`. Committed dev default is `ollama` (local model); `claude`/`ollama` fail soft to `mock`. See `backend/docs/conversational-assistant.md`. |
| `AP_ASSISTANT_OLLAMA_MODEL` | `qwen2.5:7b` | Local **tool-capable** Ollama text model for the assistant (NOT the vision model used for extraction). Base URL reuses `AP_OLLAMA_BASE_URL`. |
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
| `AP_CHAT_NOTIFICATION_PROVIDER` | `mock` | Platform-default outbound chat-notification adapter for approval events — `mock` (no network/credential — local-first default) \| `slack` \| `teams`. Per-org override + webhook URL + per-event toggles on `Organization.settings.chat_notifications`. Best-effort, PII-free, fails closed without a webhook URL. See `backend/docs/notifications.md` § Chat notifications. |
| `AP_REPORTING_CURRENCY_DEFAULT` | `USD` | Platform last-resort reporting (base) currency for multi-currency rollups when an org sets no `reporting_currency`. Per-org override on `Organization.settings.reporting_currency`. See `backend/docs/multi-currency.md`. |
| `AP_PEPPOL_INBOUND_ENABLED` | `false` | Master switch for the inbound PEPPOL AS4 receive webhook (`POST /api/peppol/inbound/{tenant_slug}`). When `false` the route is a silent no-op 204. See `backend/docs/peppol.md` § Inbound. |
| `AP_PEPPOL_INBOUND_SIGNING_SECRET` | (empty) | HMAC-SHA256 key the Access Point signs the inbound POST body with. Required when `AP_PEPPOL_INBOUND_ENABLED` is true — boot refuses otherwise. No hardcoded fallback; real secret via sops. A NON-secret dev value is committed in `backend/.env.development`. |
| `AP_PEPPOL_INBOUND_MAX_BYTES` | `4194304` | Hard cap (bytes) on the inbound PEPPOL webhook body — oversized POSTs are rejected with 204 before buffering/parsing (memory-exhaustion guard). |
| `AP_PEPPOL_PROVIDER` | `mock` | PEPPOL Access Point adapter — `mock` (in-process, no network — local-first default) \| `as4_gateway`. Per-org override on `Organization.settings.peppol.provider`. See `backend/docs/peppol.md`. |
| `AP_PEPPOL_GATEWAY_URL` | (empty) | Hosted Access Point base URL (deployed only). |
| `AP_PEPPOL_GATEWAY_API_KEY` | (empty) | PEPPOL gateway API key — **no hardcoded fallback**; sops in deployed. |
| `AP_CONTRACT_RENEWAL_ENABLED` | `false` | Master switch for the contract renewal-alert background sweep — keep `false` in local dev, flip on in deployed envs. See `backend/docs/contracts.md`. |
| `AP_CONTRACT_RENEWAL_INTERVAL_SECONDS` | `3600` | Renewal sweep interval. |
| `AP_CONTRACT_RENEWAL_DEFAULT_NOTICE_DAYS` | `30` | Platform default renewal lead window; per-contract `renewal_notice_days` overrides it. |
| `AP_VENDOR_SCREENING_ENABLED` | `true` | Synchronous sanctions screening on vendor create/update (mock-safe local-first; best-effort, never blocks the write). See `backend/docs/vendor-risk-screening.md`. |
| `AP_VENDOR_RESCREEN_ENABLED` | `false` | Master switch for the periodic vendor re-screening sweep — keep `false` in local dev, flip on in deployed envs. |
| `AP_VENDOR_RESCREEN_INTERVAL_SECONDS` | `86400` | Re-screen sweep interval. |
| `AP_VENDOR_RESCREEN_AFTER_DAYS` | `7` | Re-screen active vendors whose last screen is older than this (or never screened). |
| `AP_DISCOUNT_OPTIMIZATION_ENABLED` | `false` | Master switch for the dynamic-discounting auto-capture background sweep — keep `false` in local dev, flip on in deployed envs. The sweep only flags high-ROI offers as accepted; it never moves money. See `backend/docs/dynamic-discounting.md`. |
| `AP_DISCOUNT_OPTIMIZATION_INTERVAL_SECONDS` | `3600` | Auto-capture sweep interval. |
| `AP_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD` | `12.0` | Annualized return (APR %) an early-pay offer must clear for the sweep to auto-accept it. |
| `AP_RECURRING_INVOICES_ENABLED` | `false` | Master switch for the recurring / subscription invoice generation sweep — keep `false` in local dev, flip on in deployed envs. The sweep only generates pre-coded invoices into the approval queue; it never moves money. See `backend/docs/recurring-invoices.md`. |
| `AP_RECURRING_INVOICES_INTERVAL_SECONDS` | `3600` | Recurring-invoice generation sweep interval. |
| `AP_RECURRING_INVOICES_MAX_PER_SWEEP` | `200` | Per-tick cap on invoices generated per tenant (backlog guard). |
| `AP_STATEMENT_RECON_MATERIALITY_DEFAULT` | `1000.00` | Vendor statement reconciliation: platform-default materiality threshold (run currency, `Decimal`) above which a vendor's leftover unreconciled balance flags it not-close-ready on `GET /api/vendor-statements/close-readiness`. `?materiality=` overrides per call. No background sweep — reconciliation is user-triggered. See `backend/docs/vendor-statement-reconciliation.md`. |
| `AP_DISCOUNT_COST_OF_CAPITAL_PCT` | `8.0` | Platform-default annual cost of capital used by the ROI calculator; per-org override `Organization.settings.discounting.cost_of_capital_pct`. |
| `AP_QMS_SYNC_ENABLED` | `false` | Master switch for the QMS inspection-sync background sweep — keep `false` in local dev, flip on in deployed envs once a real QMS is configured per-org. Only upserts inspection rows; never moves money. See `backend/docs/po-matching.md` § QMS integration. |
| `AP_QMS_SYNC_INTERVAL_SECONDS` | `3600` | QMS sync sweep interval. |
| `AP_QMS_PROVIDER` | `mock` | Platform-default QMS adapter — `mock` (deterministic, no network/credential — local-first default) \| `generic` (httpx skeleton, fails closed without a key). Per-org override `Organization.settings.qms.provider`. |
| `AP_VENDOR_ENRICHMENT_PROVIDER` | `mock` | Platform-default external vendor-enrichment (firmographics) adapter — `mock` (deterministic synthetic data, no network/credential — local-first default) \| `dun_bradstreet` \| `clearbit` (httpx skeletons, **fail closed** without a per-org `api_key`; no hardcoded fallback). Per-org override `Organization.settings.enrichment.provider`. Powers `POST /api/enrichment/vendors/{id}/enrich` — advisory/suggestion-only, raw `tax_id` masked. See `backend/docs/data-enrichment.md` § External enrichment. |
| `AP_ACCESS_REVIEW_DORMANT_DAYS` | `90` | Dormancy window for the periodic SOX access review. A user holding an elevated role (`admin`/`ap_manager`/`cfo`) whose last *mutating* audit action is older than this — or who has never acted — is flagged DORMANT in `GET /api/access-reviews`. Compute-on-read (no column/migration). See `backend/docs/access-reviews.md`. |
| `AP_APPROVAL_SIGNING_KEY` | (empty) | HMAC-SHA256 key for digital signatures on invoice approvals (SOX non-repudiation). Signs the canonical approval payload (invoice id + exact amount + actor + decision + timestamp) onto each immutable `invoice.approved` audit row; re-verified at `GET /api/audit/invoice/{id}/verify-signatures`. Empty → signing skipped (no hardcoded fallback). NON-secret dev value committed in `.env.development`; real key via sops. See `backend/docs/approval-signatures.md`. |
| `AP_EMAIL_ACTION_SIGNING_KEY` | (empty) | HMAC-SHA256 key for the email-approval link token (approve/reject an assigned invoice from the notification email without logging in). Empty → feature OFF: no links added, every token rejected (fail-closed, no hardcoded fallback). NON-secret dev value committed in `.env.development`; real key via sops. The key's presence is the single on/off knob. See `backend/docs/email-approval.md`. |
| `AP_EMAIL_ACTION_TTL_HOURS` | `168` | Validity window (hours) of an email-approval link; past it the reviewer re-authenticates in the app. Also the TTL of the Slack approval-button action token (same primitive). |
| `AP_SLACK_SIGNING_SECRET` | (empty) | Slack app **signing secret** verifying the interactive-button POST to `/api/approvals/slack/interactivity` (HMAC over `v0:{X-Slack-Request-Timestamp}:{raw_body}`). Empty → Slack interactive approval is OFF: every inbound POST rejected (fail-closed, no hardcoded fallback). The button's signed action token reuses `AP_EMAIL_ACTION_SIGNING_KEY` (bound to a `slack` channel). NON-secret dev value committed in `.env.development`; real secret via sops. The key's presence is the single on/off knob. See `backend/docs/slack-approval.md`. |
| `AP_SLACK_REQUEST_MAX_AGE_SECONDS` | `300` | Reject a Slack interactivity POST whose `X-Slack-Request-Timestamp` is more than this far from now (replay-window guard). |
| `AP_RETENTION_ENABLED` | `false` | Master switch for the retention-policy enforcement sweep (SOX records management) — keep `false` in local dev, flip on in deployed envs. The sweep soft-archives overdue terminal invoices and verifies audit-log WORM shipment via a privileged, audited path; it NEVER deletes audit rows (composes with the immutability trigger). See `backend/docs/retention.md`. |
| `AP_RETENTION_INTERVAL_SECONDS` | `86400` | Retention sweep interval. |
| `AP_RETENTION_DEFAULT_MONTHS` | `84` | Platform-default retention window (months) when an org sets no per-class override on `Organization.settings.retention`. |
| `AP_PUBLIC_API_ENABLED` | `true` | Platform kill switch for the public Developer API (`/api/v1`, `X-API-Key` auth). The surface is auth-gated regardless; when `false` every key fails closed with the opaque 401. No secret — API keys are minted per-org and stored hashed. See `backend/docs/public-api.md`. |
| `AP_WEBHOOKS_ENABLED` | `false` | Master switch for **outbound** Developer-API webhooks — gates BOTH the event emit (`services/webhooks/dispatch.emit_event` → silent no-op when off, no outbound HTTP) and the background retry/delivery sweep. OFF in local dev so a fresh clone never makes outbound calls; flip on in deployed envs. No secret — each subscription's HMAC signing secret is generated at create time and stored on the `webhook_subscriptions` row (a symmetric verification key). See `backend/docs/public-api.md` § Outbound webhooks. |
| `AP_WEBHOOKS_DELIVERY_INTERVAL_SECONDS` | `60` | Outbound-webhook retry/delivery sweep tick interval. |
| `AP_BILLING_PROVIDER` | `mock` | Platform billing adapter — `mock` (in-process, deterministic, no network/credential — local-first default) \| `stripe_billing` (skeleton, fails closed without a key). Per-org override `Organization.settings.billing.provider`. See `backend/docs/billing.md`. |
| `AP_BILLING_STRIPE_API_KEY` | (empty) | Live Stripe Billing key — **no hardcoded fallback**; sops in deployed. The `stripe_billing` adapter fails closed without it. |
| `AP_BILLING_STRIPE_WEBHOOK_SECRET` | (empty) | HMAC secret for Stripe billing webhook signature verification — no fallback; sops in deployed. |

Full list in `backend/app/config.py`.

## Where to look

| Topic | Read this |
|-------|-----------|
| Frontend details | `frontend/CLAUDE.md` — routes, stores, components, API mappings |
| Backend details | `backend/CLAUDE.md` + `backend/docs/` — models, services, adapters, migrations |
| Mobile app | `mobile/CLAUDE.md` — Flutter iOS app, screens, stores, API client |
| AI extraction | `backend/docs/ai-extraction.md` — platform vs BYOK, provider configs |
| Structured e-invoicing (in + outbound) | `backend/docs/e-invoicing.md` — UBL 2.1 / Factur-X / ZUGFeRD parsing, auto-detect routing, field map; outbound UBL generate + national formats (`country_formats/`: FatturaPA·CFDI·NF-e·DIAN) via `GET /api/invoices/{id}/einvoice?format=` |
| Conversational assistant | `backend/docs/conversational-assistant.md` — fixed toolset, mock/claude adapters, token budget, audit |
| ERP integration | `backend/docs/erp-integration.md` — adapter pattern, Merge.dev, direct APIs |
| Workflow design | `backend/docs/workflow-design.md` — state machine, step types, snapshots |
| Payments | `backend/docs/payments.md` — payment runs, schedules, ERP sync |
| Virtual cards | `backend/docs/virtual-cards.md` — Lithic/Nium, rebates, webhooks |
| Dynamic discounting | `backend/docs/dynamic-discounting.md` — DiscountOffer model + migration 0043, ROI primitive, optimizer, auto-capture sweep, financing adapters, `/api/discounts` + `/discounts` dashboard |
| Recurring / subscription invoices | `backend/docs/recurring-invoices.md` — RecurringInvoiceTemplate model + migration 0046, the `(template, period_key)` idempotency index, generation sweep + env vars, variance signal, `/api/recurring` API + `/recurring` route |
| Vendor statement reconciliation | `backend/docs/vendor-statement-reconciliation.md` — supplier statement-of-open-items ↔ AP ledger; pure engine + classifications, `VendorStatementReconciliation`/`Line` model + migration 0047, recon-lines-not-Exceptions design note, `/api/vendor-statements` API + `/vendor-statements` route, close-readiness period-close gate |
| Positive Pay / payment-fraud file | `backend/docs/positive-pay.md` — check-issue + ACH-authorization export, `PositivePayFile` model + migration 0048 + `uq_positive_pay_run_format` idempotency index, pluggable per-bank formatter adapters (`positive_pay_adapters/`: csv \| fixed_width), pure return classifier (altered → invoice-scoped `fraud_flag`; never-issued → standalone invoice-less `fraud_flag`, enabled by migration 0049 making `Exception.invoice_id` nullable), PII handling, `/api/positive-pay` API + `/positive-pay` route |
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
| Data privacy (GDPR/CCPA) | `backend/docs/privacy.md` (DSAR + erasure), `docs/data-residency.md` (region pinning), `docs/ropa.md` (Record of Processing Activities), `docs/sub-processors.md` (sub-processor register) |
| Platform billing & metering (plans / subscriptions / entitlements) | `backend/docs/billing.md` |
| White-label / partner branding | `docs/white-label.md` — per-tenant product name / logo / accent colors on `settings.brand`, `/api/organization/branding`, frontend theming via CSS custom properties |
| Founder runbooks (non-code) | `docs/founder-runbooks/` — legal, prod deploy, Stripe, payment rails, SOC 2 vendor, support + status, DPA template, breach-notification runbook |
| Accessibility (WCAG 2.2 AA) | `docs/accessibility.md` (conformance statement) + `docs/accessibility-vpat.md` (VPAT/ACR). Web baseline lives in the shared `frontend/src/lib/components/` + `app.css` (see `frontend/CLAUDE.md` → Accessibility patterns); regression guards are `frontend/tests-e2e/a11y/` (axe-core) + `mobile/test/a11y/` (`meetsGuideline`) |
| CSV data import | `backend/docs/csv-import.md` — pilot Day-0 vendor + invoice migration |
| Email-to-invoice intake | `backend/docs/email-intake.md` — per-tenant inbound address, SES + Mailgun setup |
| Contract Management (CLM) | `backend/docs/contracts.md` — lifecycle, Contract/ContractLineItem model, repository + upload, spend-to-contract tracking, renewal sweep + env vars, compliance (`contract_noncompliant`), contract-based PO creation, migrations 0036/0037 |
| Expense Management | `backend/docs/expense-management.md` — five-table model (expenses/reports/policies/card-transactions/preapprovals, migration 0039), circular FK via `use_alter`, `/expenses` + `/expense-reports` + `/expense-policies` + `/expense-preapprovals` API, receipt upload + cross-tenant download, total recompute, WF3 policy engine (`services/expense_policy.py`) + report submit/approve/reject (segregation + CFO threshold), WF4 roadmap |
| Automated E-Invoicing (PEPPOL send + receive) | `backend/docs/peppol.md` — four-corner model, mock/as4_gateway adapters, ParticipantId, BIS Billing 3.0, transmission model + idempotency guard, send route, inbound AS4 receive webhook (C4 corner), HMAC-gated, MessageId dedupe, routes to einvoice extractor |
| 1099 tracking | `backend/docs/tax-1099.md` — W-9 collection, YTD reporting, Tax1099 integration sketch |
| Audit-log shipping | `backend/docs/audit-log-shipping.md` — centralized WORM sink, adapters, S3 Object Lock caveats |
| Notifications | `backend/docs/notifications.md` — email + in-app events, the `transition_invoice` hook, recipient matrix, preferences |
| Exception agents | `backend/docs/exception-agents.md` — autonomous exception resolution, autonomy thresholds, `AgentDecision` log, amount-mismatch resolver |
| Adaptive AI workflows | `backend/docs/adaptive-workflows.md` — approval-pattern learning, baseline anomaly read, advisory suggestions (advisory-only, no LLM) |
| Data enrichment | `backend/docs/data-enrichment.md` — auto-fill (GL/cost-center/terms), line-item price variance, vendor performance scoring, duplicate/similar vendor consolidation clustering — all advisory/compute-on-read from supplier history — plus external firmographics enrichment (D&B/Clearbit via `enrichment_adapters/`, mock local-first default, `POST /api/enrichment/vendors/{id}/enrich`, advisory-only) |
| Backup + DR | `docs/backup-disaster-recovery.md` — RTO/RPO, restore procedures, test cadence |
| Secrets rotation | `docs/secrets-rotation.md` — what to rotate, when, and how |
| Getting started | `docs/getting-started.md` — first-run setup |
| Troubleshooting | `docs/troubleshooting.md` — common issues |
| Self-service signup | `docs/self-service-signup.md` — signup flow, email adapters, abuse mitigations |
| Supplier portal | `backend/docs/supplier-portal.md` — VendorUser auth, invoice submission, phase 2 deferrals |
| Supplier chat & collaboration | `backend/docs/supplier-chat.md` — per-invoice AP↔supplier thread, author polymorphism, attachment key scheme + cross-tenant gate, audit actions, `chat_message` notification + supplier portal-link email, `Organization.settings.supplier_chat.enabled` flag, static templates |
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
13. **One worktree per concurrent session; merge it back to `main`.** When more
    than one Claude (or person) works this repo at once, each session runs in its
    own git worktree (`claude --worktree <name>`) — never two sessions in the
    shared checkout. The scope-guard is path-granular, not hunk-granular, so two
    sessions editing the *same file* in one checkout silently capture each
    other's edits (a path-scoped commit grabs whatever is in the one shared tree);
    a worktree is the only real fix. Then **consolidate**: a worktree commits on
    its own branch and only reaches `main` via an explicit `git merge` from the
    primary checkout — retiring a worktree does NOT merge it. Before ending such
    work, run `git branch --no-merged main` and merge anything still off `main`.
    The `SessionStart` hook `.claude/hooks/unmerged-worktree-check.sh` is the
    backstop: it warns at every session start about branches holding commits not
    on `main` — when it fires, surface it and offer to consolidate. See [Running
    concurrent sessions — use a worktree](#running-concurrent-sessions--use-a-worktree).

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
