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
| International payments (FX + SEPA + SWIFT) | `docs/international-payments.md` |
| Multi-currency reporting (reporting currency + unrealized FX) | `docs/multi-currency.md` |
| International tax (VAT / GST / withholding) | `docs/international-tax.md` |
| Bank reconciliation | `docs/bank-reconciliation.md` |
| Analytics + CFO dashboard + CSV + scheduled reports | `docs/analytics.md` |
| Virtual cards (Lithic / Nium) | `docs/virtual-cards.md` |
| PO matching (2-way / 3-way) | `docs/po-matching.md` |
| Vendor management | `docs/vendor-management.md` |
| Local AI testing (Ollama) | `docs/local-ai-testing.md` |
| Docker Compose services | `docs/docker.md` |
| Redis | `docs/redis.md` |
| MinIO / S3 | `docs/minio.md` |
| Audit-log shipping (SOC 2) | `docs/audit-log-shipping.md` |
| Audit-log summarization (invoice modal) | `docs/audit-summary.md` |
| Email + in-app notifications | `docs/notifications.md` |
| Exception agents (autonomous resolution) | `docs/exception-agents.md` |
| Adaptive AI workflows | `docs/adaptive-workflows.md` |
| Data enrichment (auto-fill, price variance, vendor scoring) | `docs/data-enrichment.md` |

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
docker compose up -d          # Postgres, Redis, MinIO (core)
docker compose --profile idp up -d keycloak   # opt-in local OIDC IdP (pnpm idp:up); see docs/docker.md
python main.py                # dev server :8000 (auto-reload via uvicorn)
pytest                        # run tests
ruff check . && ruff format . # lint + format

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head                                # control plane
AP_MIGRATE_TENANT=ap_acme alembic upgrade head      # single tenant
python scripts/migrate_all_tenants.py               # all tenants
```

## Dependency lock (CI hash-pinning)

Local dev installs editable extras (`pip install -e ".[dev]"`) — unchanged.
**CI and the production image** install from hash-pinned locks (every
third-party artifact pinned by hash) to satisfy Scorecard's
Pinned-Dependencies supply-chain check. Two locks, both regenerated from
`pyproject.toml`:

| Lock | Scope | Consumed by |
|------|-------|-------------|
| `requirements-dev.lock` | base + `[dev]` extra + pip | `ci.yml` — `pip install --require-hashes …` then `pip install -e . --no-deps` |
| `requirements.lock` | base runtime only (no extras) | `backend/Dockerfile` — `uv pip install --system --require-hashes …` (app runs from source, no editable install) |

Regenerate **both** whenever you change `pyproject.toml` dependencies (or
the pinned pip version in `requirements-dev.in`):

```bash
# from backend/ — uv resolves universally for the runtime Python (3.14)
uv pip compile pyproject.toml requirements-dev.in --extra dev \
  --universal --python-version 3.14 --generate-hashes -o requirements-dev.lock
uv pip compile pyproject.toml \
  --universal --python-version 3.14 --generate-hashes -o requirements.lock
```

`uv` not installed? `pipx run uv pip compile …` works ephemerally. Commit
the regenerated locks in the same change as the `pyproject.toml` edit, or
the `--require-hashes` installs (CI + image build) fail.

## Project structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app, CORS, router includes, lifespan
│   ├── config.py            # Pydantic Settings (AP_ prefix env vars)
│   ├── database.py          # Control engine + per-tenant engine pool
│   ├── redis.py             # Redis connection + token blocklist
│   ├── tenant.py            # X-Tenant-Slug → tenant DB session
│   ├── api/                 # FastAPI routers (one per domain) + deps.py
│   │                        #   (deps.py: JWT auth, get_current_user, get_org_id)
│   ├── models/              # SQLAlchemy ORM models
│   ├── schemas/             # Pydantic request/response models
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
   - `User` — email, full_name, hashed_password, sso_provider/id, mfa_secret/enabled/enrolled_at, must_change_password, notification_prefs (JSONB — per-user email/in-app channel prefs, user-global), organization_id
   - `Role` — name (admin, ap_manager, ap_clerk, cfo)
   - `UserRole` — junction table
   - `ExtractionUsage` — billing: invoice_id, provider, program_type, period
   - `CardRebate` — virtual_card_id, amount, rate, status, period

2. **Tenant DBs** (`ap_<slug>`) — isolated per customer
   - `Entity` — legal entity / subsidiary within the tenant (name, slug, currency, is_default, is_active). Business tables carry a nullable `entity_id` FK (`EntityMixin`); every tenant has one `is_default` Entity. Multi-entity Phase 2 (reads/writes scoped by the `X-Entity-ID` header) — see `../docs/multi-entity.md`
   - `Invoice` — invoice_number, vendor_name, amount, status (12 states), file_key, warnings (JSONB), po_match (JSONB), meta (JSONB — holds `audit_summary`)
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
   - `CreditMemo` — vendor_id, original_invoice_id, amount, status (open/applied/voided)
   - `BankStatement` / `BankTransaction` — uploaded statement + parsed transactions for reconciliation
   - `SanctionsCheck` — append-only KYC / sanctions screening trail per vendor
   - `ScheduledReport` — recurring CFO report definition (cron, recipients, format)
   - `InvoiceEmbedding` — vector embedding per invoice for RAG / duplicate detection
   - `VendorExtractionPrior` — accumulated vendor field priors that bias the next extraction
   - `VendorUser` — supplier-portal credentials scoped to a single Vendor
   - `VendorChangeRequest` — staged supplier-portal change to a vendor's `bank_details` / `tax_id`, pending AP approval (migration 0022; fraud-prevention gate — see `docs/supplier-portal.md`)
   - `CardRevealToken` — single-use token granting vendor access to a virtual-card PAN reveal page
   - `Notification` — in-app notification center rows (recipient_user_id, event_type, entity_id, title/body, read_at). See `docs/notifications.md`

**Connection management** (`database.py`):
- `get_control_db()` → AsyncSession for control plane
- `get_tenant_db()` → AsyncSession for tenant (via `X-Tenant-Slug` header)
- Engine pool: `pool_size=5, max_overflow=10` per tenant; `pool_size=10, max_overflow=20` for control
- All engines disposed on app shutdown

## Invoice workflow state machine

```python
VALID_TRANSITIONS = {
    new:                {pending, ready_for_review, approved, done},
    pending:            {ready_for_review, approved, failed},
    ready_for_review:   {approved, rejected},
    approved:           {sending_to_erp, payment_scheduled, done},
    rejected:           {ready_for_review, new},
    sending_to_erp:     {sent_to_erp, failed},
    sent_to_erp:        {posted_in_erp, done},
    posted_in_erp:      {payment_scheduled, done},
    payment_scheduled:  {paid, approved},      # void → back to approved
    paid:               {done, approved},      # void → back to approved
    done:               {},                    # terminal
    failed:             {pending, sending_to_erp},
}
```

`payment_scheduled → approved` and `paid → approved` are back-edges
used by the void-payment path (`POST /api/payments/{id}/void`) to
re-enter the payment queue. Everything else is forward-only.

Step types: `extraction` → `approval` → `erp_export` → `done`

`workflow_engine.py` functions: `validate_transition()`, `transition_invoice()`, `get_invoice_for_update()` (SELECT...FOR UPDATE), `create_workflow_instance()`, `advance_workflow()`, `is_step_enabled()`.

**Snapshot pattern**: `WorkflowInstance.steps_config_snapshot` freezes the active definition at invoice creation. All runtime logic reads the snapshot, not the live definition.

**Notification hook**: `transition_invoice()` is also the single chokepoint for invoice-event notifications — after the audit write it calls `notification_dispatch.notify_event()` keyed off the resulting status (`approved`/`rejected`/`paid`). The `invoice_assigned` event is fired separately from `review.assign_reviewer`. All best-effort (never breaks the transition). See `docs/notifications.md`.

## Key background services

| Service | What it does |
|---------|-------------|
| `services/extraction_reaper.py` | Sweeps every tenant DB on a timer; transitions invoices stuck in `pending` extraction to `failed`. |
| `services/audit_log_shipper.py` | Centralized audit-log shipper (SOC 2). Sweeps every tenant DB, reads unshipped `audit_log` rows in batches, fans them out to every configured `audit_shipping` adapter (CloudWatch Logs + S3 Object Lock), then marks `shipped_at=now()`. All adapters must ACK before rows are marked; failures leave rows unshipped so the next tick retries. Disabled by default — flip `AP_AUDIT_SHIPPING_ENABLED` on in deployed envs. See `docs/audit-log-shipping.md`. |
| `services/approval_escalation.py` | Sweeps every tenant's active workflow instances and appends `escalation_to_user_ids` onto any approval chain level waiting longer than its configured `escalation_hours`. Disabled by default (`AP_APPROVAL_ESCALATION_ENABLED`); flip on in deployed envs. |
| `services/payment_reconciler.py` | Backstop polling for payments whose processor webhook went missing. Re-fetches status from the payment adapter when a `submitted`/`processing` payment sits longer than `AP_PAYMENT_RECONCILE_AFTER_MINUTES`. Disabled by default (`AP_PAYMENT_RECONCILE_ENABLED`); flip on in deployed envs alongside Modern Treasury. |

All four are long-lived asyncio tasks started in `main.lifespan` and cancelled on shutdown.

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

### Payment adapters (`services/payment_adapters/`)

```python
@register_payment_adapter("my_processor")
class MyAdapter(PaymentAdapter):
    async def create_payment(self, payload: PaymentPayload) -> PaymentResult: ...
    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus: ...
    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock`, `modern_treasury`, `stripe_treasury`, `increase`, `column`, `dwolla` (ACH only), `checkeeper` (check printing).

`execute_payment_run` dispatches via the adapter; webhook handler at `/api/payments/webhook/{tenant_slug}/{provider}` drives the `submitted → completed/failed` transition. Tenant comes from the URL path (no JWT, no header). Idempotent on the payment's `correlation_id`.

Per-org config in `Organization.settings.payments`. See `../docs/payments.md` § Payment processor adapters.

### FX rate adapters (`services/fx_adapters/`)

```python
@register_fx_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"
    def __init__(self, config: dict | None = None): ...
    async def get_rate(self, source: str, target: str) -> FXRate: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock`, `openexchangerates`. Wise / Tipalti slot in via the same pattern.

`services/international_payments.prepare_international_payment` calls `get_rate` exactly once at payment-submission time, persists the locked rate + `fx_locked_at` on the Payment row, and never re-fetches even if the market moves before settlement. The corridor selector decides whether an FX leg is needed (`requires_fx` on `CorridorChoice`); same-currency payments skip the lookup entirely. Per-org config in `Organization.settings.fx`. See `docs/international-payments.md`.

### Sanctions / KYC adapters (`services/sanctions_adapters/`)

```python
@register_sanctions_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"
    def __init__(self, config: dict | None = None): ...
    async def screen_vendor(self, *, vendor_name, vendor_country, vendor_tax_id=None,
                            beneficial_owners=None) -> ScreeningResult: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock`, `complyadvantage` (skeleton — live key required). Same registry pattern as the others.

`services/compliance.check_payment_compliance` is called by `execute_payment_run` between `prepare_international_payment` and `adapter.create_payment`. A `match` verdict refuses the payment outright; a `review_required` puts it on hold (`status="pending_compliance"`). Every screening writes an append-only `sanctions_checks` row. Per-org config in `Organization.settings.compliance`. See `docs/international-payments.md` § KYC / AML compliance.

### Audit-shipping adapters (`services/audit_shipping/`)

```python
@register_audit_shipping_adapter("my_sink")
class MySinkAdapter(AuditShippingAdapter):
    async def ship(self, rows: list[AuditLogRow]) -> None: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock`, `cloudwatch`, `s3_objectlock`.

The `audit_log_shipper` background loop instantiates every adapter named in `AP_AUDIT_SHIPPING_PROVIDERS` and ships each batch to all of them; all must succeed before the rows are marked shipped. See `docs/audit-log-shipping.md`.

### TIN-validation adapters (`services/tin_validation_adapters/`)

```python
@register_tin_validation_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"
    def __init__(self, config: dict | None = None): ...
    async def validate(self, *, tin, legal_name=None, tin_type_hint=None) -> TINValidationResult: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock` (offline EIN/SSN format + IRS structural rules — the local-first default), `tax1099` (IRS TIN-match skeleton — live key required; degrades to format-only without a key). Selected per-org via `Organization.settings.tax.tin_validation` → falls back to `AP_TIN_VALIDATION_PROVIDER` (default `mock`). Results carry only the verdict + redacted last-4 — never the raw TIN. Wired at `POST /api/tax/vendors/{id}/tin-verify`. See `docs/tax-1099.md`.

### 1099 e-filing adapters (`services/tax_filing_adapters/`)

```python
@register_tax_filing_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"
    def __init__(self, config: dict | None = None): ...
    async def submit_batch(self, *, tax_year, forms, idempotency_key) -> FilingBatchResult: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock` (offline, deterministic, idempotent — the local-first default), `tax1099` (partner e-file skeleton — live key required). Selected per-org via `Organization.settings.tax.filing` → falls back to `AP_TAX_FILING_PROVIDER` (default `mock`). `POST /api/tax/1099/file` is idempotent on `(organization_id, idempotency_key)` via the `tax_1099_filings` table (a duplicate IRS filing is a real problem); the filing row carries no recipient TIN. See `docs/tax-1099.md`.

### Exception-agent resolvers (`services/exception_agents/`)

```python
@register_exception_agent("po_mismatch")
class AmountMismatchResolver(ExceptionResolver):
    agent_type = "amount_mismatch_v1"
    exception_type = "po_mismatch"
    async def evaluate(self, db, *, exception, invoice, org_settings) -> AgentEvaluation: ...
    async def apply(self, db, *, exception, invoice, evaluation, actor_id) -> None: ...
```

Registry by `exception_type` (`@register_exception_agent`). The `coordinator.run_agent` dispatches by exception type, gates auto-resolve on the org's `autonomy_level` → confidence threshold, and writes an append-only `AgentDecision` row every run; auto-resolves also write the DB-immutable `invoice.approved` audit row via `review.approve_invoice`. Registered: `amount_mismatch_v1` (real — `po_mismatch` amount variance), plus escalate-only stubs for `missing_data`, `duplicate`, `fraud_flag`. Local-first: the optional LLM rationale fails soft to a deterministic template with no key. See `docs/exception-agents.md`.

## Webhook security (`services/webhook_security.py`)

Every inbound webhook handler — payments, cards, ERP, email-intake — verifies the provider's HMAC over the raw request body and dedupes by event id before mutating state (project invariant #9). Shared helpers:

- `verify_hmac_sha256(secret, raw_body, provided_hex)` — constant-time HMAC-SHA256 check via `hmac.compare_digest`. Empty / missing secret or signature fail closed.
- `is_event_already_processed(provider, event_id, ttl_seconds=86400)` — Redis `SET NX EX` dedup. First delivery returns `False`; replays within the TTL window return `True` so the handler short-circuits.
- `extract_signature_header(headers, *candidates)` — case-insensitive multi-candidate header lookup (different providers use different header names).

Per-tenant secrets:

| Endpoint | Settings path |
|---|---|
| `/api/payments/webhook/...` | `Organization.settings.payments.webhook_secret` (verified inside the adapter's `parse_webhook`) |
| `/api/cards/webhook/{provider}` | `Organization.settings.cards.webhook_signing_secret` |
| `/api/erp/webhook/{erp_type}` | `Organization.settings.erp.webhook_signing_secret` |

Every webhook handler returns **204 silently** on every rejection path (bad signature, unknown tenant, missing event id, unknown card / invoice / payment). Distinct 4xx responses would enumerate tenant slugs or card tokens. Tests: `backend/tests/test_webhook_security.py`, `tests/test_payment_webhook_security.py`.

## Security utilities

- **Passwords**: a single shared `pwd_context` in `app/utils/passwords.py` uses `bcrypt_sha256` (SHA-256 pre-hash → bcrypt) to side-step bcrypt's 72-byte truncation. Every call site (auth, admin, portal, vendors, tenant_provisioning, scripts/seed) imports from there — never construct a fresh `CryptContext`. Complexity rules in `validate_password_complexity` (min 12 chars, upper/lower/digit).
- **Filename sanitiser**: `app/services/storage.py::_safe_filename` strips path separators, `..`, leading dots (no dotfiles), and control / non-printable characters. Used by `upload_invoice_file` before interpolating the filename into the S3 key. Without it, a vendor portal POST with filename `../../other-org/secret.pdf` could land under another tenant's prefix.
- **File download cross-tenant check**: `GET /api/workflow/file/{file_key:path}` verifies the key's first segment equals the requesting user's `organization_id`. Same 404 for wrong-org and missing-file so the response doesn't enumerate prefixes.

## Audit immutability + access auditing (SOX)

The `audit_log` table is **append-only at the database level**: migration `0022_sox_audit_immutable` installs `BEFORE` triggers (DDL in `app/services/audit_immutability.py`) that reject every DELETE and every UPDATE touching a column other than `shipped_at`. The `shipped_at` carve-out lets `audit_log_shipper.py` stamp shipped rows; everything else is frozen, so a rogue ORM call or a direct `psql` session can't tamper with the trail. Installed on every tenant DB — migration fan-out for existing tenants, `tenant_provisioning._create_tenant_tables` for fresh ones (which use `create_all`, not Alembic). See `docs/audit-log-shipping.md`.

Two request-path helpers in `app/services/audit_access.py` (thin wrappers over `dispatch_audit`, not reimplementations):
- `log_access(...)` — writes a `<entity_type>.viewed` row for SOX access-control auditing. Instrumented reads: vendor detail (`vendor.viewed`), payment detail (`payment.viewed`), card PAN reveal (`card.details_viewed`), the audit-trail view (`audit.viewed`), and every auditor export (`audit.exported`). The `details` payload records the field-**names** accessed, never the values — no tax id / bank number / PAN ever enters the audit trail (PII-out-of-logs).
- `build_field_diff(before, after, fields)` — produces `{field: {old, new}}` for SOX change history on invoice edits + approve-with-corrections. Money serialises as **string-Decimal**, never float.

The auditor-export surface is `app/api/audit.py` (`/api/audit/export`, `/api/audit/invoice/{id}` — GET-only, admin/CFO). See `docs/api-reference.md` § Audit Trail.

## Dispatch modes

Extraction, ERP push, and audit logging support two execution modes:
- **local** (default) — jobs queued in-process; pool of 3 worker threads drains the queue. Each worker creates fresh engines with `pool_size=1, max_overflow=0` to avoid exhausting PostgreSQL connections.
- **lambda** — sends message to SQS, processed by Lambda handler

Files: `*_dispatch.py` (router), `*_lambda.py` (Lambda handler).

## Authentication (`api/deps.py`)

- JWT HS256 signed with `AP_SECRET_KEY`, 30-min expiry (configurable)
- Token payload: `sub` (user_id), `org` (org_id), `jti` (unique ID for blocklist)
- `get_current_user()` — FastAPI dependency, returns User or 401
- Logout adds `jti` to Redis blocklist with TTL matching token expiry

### RBAC (`require_roles`)

Every authenticated endpoint declares the roles it accepts:

```python
from app.api.deps import ROLE_ADMIN, ROLE_AP_MANAGER, require_roles

user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER))
```

- Any-of semantics: user passes if they hold at least one listed role.
- 403 with `{"detail": "Your role does not permit this action."}` on miss.
- Denials log at WARN level for monitoring.
- New endpoints without an auth dependency fail `tests/test_rbac.py` — coverage gate.
- Public endpoints (login, MFA challenge, OIDC, signup, webhooks, SCIM) live in `NO_AUTH_REQUIRED` in the same test file.
- Full permission matrix: `../docs/authentication.md` § RBAC.

### MFA (`services/mfa.py`)

- TOTP (pyotp) + email-OTP backup. Master switch `AP_MFA_ENABLED` (default `false` for local dev).
- Per-user secret on `User.mfa_secret`; org-wide enforcement via `Organization.settings.mfa.required`.
- Login returns either `TokenResponse` or `MFAChallengeResponse`. Challenge token is a short-lived JWT with `typ: mfa_challenge` — verified at `POST /api/auth/mfa/verify`.
- Email-OTP hashes live in Redis (`mfa:email_otp:<user_id>`), short TTL, single-use.
- SSO sign-in skips our MFA challenge — IdPs handle their own MFA.
- Full reference: `../docs/authentication.md` § MFA.

### SSO — OIDC + SAML (`api/auth_sso.py`, `api/auth_saml.py`, `services/sso.py`)

- Per-tenant config in `Organization.settings.sso`, discriminated by `protocol`
  (absent / `"oidc"` → OIDC; `"saml"` → SAML). `resolve_sso_config` /
  `resolve_saml_config` each return `None` for the other protocol.
- Both protocols share the identity tail in `services/identity_provisioning.py`
  (`jit_provision` + `extract_and_check_email`) and the session-mint tail — only
  IdP-response *verification* differs.
- SAML verification (`auth_saml.py`) is `python3-saml` pinned to a hardened
  posture: `wantAssertionsSigned`, SHA-256-only, issuer/audience/destination +
  mandatory InResponseTo, per-tenant replay dedup, IdP cert pinned (no
  fingerprint/embedded), XXE-hardened parsing. SP signing keypair (optional) →
  `AP_SAML_SP_*` via sops. Local IdP: Keycloak (`pnpm saml:seed`).
- Full reference: `../docs/authentication.md` § SAML SSO + `../docs/local-sso-saml.md`.

## Organization settings (JSONB)

Stored in `Organization.settings`:
```json
{
  "company": { "name", "tax_id", "address", "phone", "website", "logo_url" },
  "invoice_defaults": { "currency", "payment_terms", "number_prefix", "default_gl_account", "default_cost_center" },
  "erp": { "type", "integration_method", "credentials": { ... }, "webhook_signing_secret": "..." },
  "extraction": { "program_type": "platform"|"byok", "provider", "api_key", "model" },
  "cards": { "enabled": true|false, "program_type": "platform"|"byok", "provider", "region": "US"|"EU"|...,
             "default_expiry_days": 30, "webhook_signing_secret": "...", ... },
  "payments": { "provider", "credentials": { ... }, "webhook_secret": "...", "cfo_approval_above": Decimal },
  "mfa": { "required": true|false },
  "sso": { ... },
  "fraud_rules": { ... },
  "exception_agents": { "autonomy_level": "conservative"|"balanced"|"aggressive", "amount_tolerance_pct": 2.5 }
}
```

The three `webhook_*_secret` fields are HMAC keys used by the inbound webhook handlers — see "Webhook security" above.

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
- `services/rate_limit.py` — Redis sliding-window limiter, keyed on `(endpoint, subject)` where `subject` defaults to client IP but can be an explicit value (e.g. email). Signup uses three limits: per-IP `/start` + `/complete` (`AP_SIGNUP_RATE_LIMIT_PER_HOUR`, default 5), per-email `/start` (`AP_SIGNUP_EMAIL_RATE_LIMIT_PER_HOUR`, default 3, anti email-bombing), and per-IP `/slug-check` (`AP_SLUG_CHECK_RATE_LIMIT_PER_HOUR`, default 120, anti-enumeration).
- `utils/slug.py` — regex + reserved-word blocklist + DB uniqueness check.
- `utils/hcaptcha.py` — server-side siteverify. Skips when `AP_HCAPTCHA_SECRET` is empty (local dev).
- `utils/passwords.py` — `generate_temp_password()` + `validate_password_complexity()` (min 12 chars, upper/lower/digit).

The captcha sitekey is exposed to the frontend via `GET /api/public-config` so the SvelteKit build doesn't need to bake it in.

Relevant env vars: `AP_ENVIRONMENT` (deployed envs refuse to boot with an empty `AP_HCAPTCHA_SECRET`), `AP_EMAIL_PROVIDER`, `AP_EMAIL_FROM`, `AP_AWS_SES_REGION`, `AP_PUBLIC_URL`, `AP_TENANT_URL_TEMPLATE`, `AP_HCAPTCHA_SECRET`, `AP_HCAPTCHA_SITEKEY`, `AP_SIGNUP_RATE_LIMIT_PER_HOUR`, `AP_SIGNUP_EMAIL_RATE_LIMIT_PER_HOUR`, `AP_SLUG_CHECK_RATE_LIMIT_PER_HOUR`.

## Secrets management (SOPS + AWS KMS)

Deployed-environment secrets are encrypted at rest with [SOPS](https://github.com/getsops/sops) backed by an AWS KMS key. Local dev needs no secret setup: `backend/.env.development` is **committed** with safe, no-risk local defaults and is loaded by `main.py` (the local-dev entrypoint) via `python-dotenv`, so a fresh clone runs immediately. A gitignored `backend/.env` holds personal overrides and wins over the committed defaults. The backend also runs straight off `app/config.py` defaults even with neither file present.

**File layout:**

```
backend/
├── .env.development     # local dev defaults — committed (safe, no-risk only); loaded by main.py
├── .env                 # personal local overrides — gitignored; wins over .env.development
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
