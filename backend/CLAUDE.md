# Backend — CLAUDE.md

Backend-specific guidance. See root `CLAUDE.md` for project-wide context.

## Where to look (backend docs)

Deep-dive docs live in `backend/docs/`:

| Topic | File |
|-------|------|
| REST API reference | `docs/api-reference.md` |
| PostgreSQL schema + migrations | `docs/database.md` |
| AI extraction adapters | `docs/ai-extraction.md` |
| Inbound structured e-invoicing (UBL / Factur-X / ZUGFeRD) | `docs/e-invoicing.md` |
| Conversational AP assistant | `docs/conversational-assistant.md` |
| AI Cash-Flow Copilot (Phase 1 — read-only cash tools + `/api/cash-flow` façade) | `../docs/cash-flow-copilot.md` (repo-root `docs/`) |
| ERP adapters (Merge.dev + direct) | `docs/erp-integration.md` |
| Workflow state machine | `docs/workflow-design.md` |
| Workflow snapshot semantics | `docs/workflow-snapshots.md` |
| Payment runs + ERP sync | `docs/payments.md` |
| Dynamic discounting & early-payment optimization | `docs/dynamic-discounting.md` |
| Recurring / subscription invoices | `docs/recurring-invoices.md` |
| International payments (FX + SEPA + SWIFT) | `docs/international-payments.md` |
| Multi-currency reporting (reporting currency + unrealized FX) | `docs/multi-currency.md` |
| International tax (VAT / GST / withholding) | `docs/international-tax.md` |
| Bank reconciliation | `docs/bank-reconciliation.md` |
| Vendor statement reconciliation | `docs/vendor-statement-reconciliation.md` |
| Positive Pay / payment-fraud file | `docs/positive-pay.md` |
| Analytics + CFO dashboard + CSV + scheduled reports | `docs/analytics.md` |
| Custom (ad-hoc) report builder | `docs/report-builder.md` |
| Virtual cards (Lithic / Nium) | `docs/virtual-cards.md` |
| PO matching (2-way / 3-way) | `docs/po-matching.md` |
| Vendor management | `docs/vendor-management.md` |
| Local AI testing (Ollama) | `docs/local-ai-testing.md` |
| Docker Compose services | `docs/docker.md` |
| Redis | `docs/redis.md` |
| MinIO / S3 | `docs/minio.md` |
| Audit-log shipping (SOC 2) | `docs/audit-log-shipping.md` |
| Periodic access reviews (SOX) | `docs/access-reviews.md` |
| Audit-log summarization (invoice modal) | `docs/audit-summary.md` |
| Email + in-app notifications | `docs/notifications.md` |
| Email approval (approve/reject from the email, no login) | `docs/email-approval.md` |
| Slack interactive approval (approve/reject from Slack buttons, no login) | `docs/slack-approval.md` |
| Exception agents (autonomous resolution) | `docs/exception-agents.md` |
| Adaptive AI workflows | `docs/adaptive-workflows.md` |
| Data enrichment (auto-fill, price variance, vendor scoring) | `docs/data-enrichment.md` |
| PEPPOL AS4 outbound (e-invoice transmission) | `docs/peppol.md` |
| Contract management (CLM) | `docs/contracts.md` |
| Expense management | `docs/expense-management.md` |
| Digital signatures on approvals (SOX) | `docs/approval-signatures.md` |
| Retention policies (SOX records management) | `docs/retention.md` |
| Privacy — GDPR/CCPA DSAR export + right-to-erasure | `docs/privacy.md` |
| Public Developer API (API keys + `/api/v1`) | `docs/public-api.md` |
| Platform billing & metering (plans / subscriptions / entitlements) | `docs/billing.md` |

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

## CI test sharding

The full suite (~3900 tests against a real Postgres/Redis/MinIO) ran ~27 min as
a single serial job — the longest job in CI. In `ci.yml` it's split into:

- **`backend-lint`** — `ruff check` + `ruff format --check` only. No services,
  ~30s, fails fast on a formatting/lint miss.
- **`backend-test`** — a `strategy.matrix` of 4 shards, each on its own runner
  booting its OWN Postgres + Redis + MinIO and running a deterministic slice via
  [`pytest-split`](https://pypi.org/project/pytest-split/): `pytest --splits 4
  --group ${{ matrix.shard }}`. Each shard is a separate process + DB, which is
  why this is safe where in-process `pytest -n auto` is not — the suite's realdb
  fixtures hit event-loop-per-worker hazards under xdist. ~27 min ÷ 4 ≈ ~7 min/shard.

`pytest-split` partitions by a committed `backend/.test_durations` baseline when
present; absent, it falls back to an even split by **test count** (still correct
and deterministic — every test runs in exactly one shard — just less
wall-clock-balanced). To regenerate the baseline for better balance (e.g. after a
large test-surface change), run the full suite once with the DB stack up:

```bash
# from backend/, stack up (docker compose up -d) and venv active
pytest --store-durations          # writes backend/.test_durations
```

Commit the updated `.test_durations` alongside the test changes. Bumping the
shard count means editing the `matrix.shard` list, the `--splits N` flag, and the
`name:` (`shard N/4`) together in `ci.yml`.

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
   - `WebAuthnCredential` — registered passkey (credential_id, public_key, sign_count, transports) per `user_id`; the WebAuthn second factor (migration 0063)
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
   - `QualityInspection` — inspection_number, po_id, gr_id, result (pass/fail/partial), accepted_quantity, rejected_quantity, deviation_notes — the 4-way match leg (see `docs/po-matching.md`)
   - `GLAccount` — code, name, account_type, parent_code, erp_account_id
   - `PaymentRun` — status, total_amount, initiated_by, executed_at
   - `PaymentSchedule` — invoice_id, due_date, discount_date, discount_percent
   - `Payment` — invoice_id, payment_run_id, amount, method (ach/wire/check/virtual_card), status
   - `VirtualCard` — invoice_id, card_provider (lithic/nium), provider_card_id, amount_limit, status
   - `WorkflowDefinition` — name, steps_config (JSONB), is_active, is_default
   - `WorkflowInstance` — definition_id, invoice_id, current_step, state, steps_config_snapshot (JSONB)
   - `WorkflowStep` — instance_id, step_number, step_type, assigned_to, action, completed_at
   - `WorkflowExperiment` — A/B test of two workflow-rule configs (`config_a`/`config_b` JSONB) on one `workflow_definition_id`; `split_a_pct`, `primary_metric`, `min_sample_per_variant`, `status` (draft/running/concluded), `assignments` (JSONB `{invoice_id: "A"|"B"}`). Assigned at invoice creation (deterministic stable hash, freezes the variant config onto the instance snapshot); migration 0064. See `docs/adaptive-workflows.md` § A/B testing
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
   - `PeppolTransmission` — one row per PEPPOL transmission (direction=outbound|inbound). Outbound idempotency: partial unique index `uq_peppol_one_live_per_invoice_direction` on `(invoice_id, direction) WHERE status <> 'failed'`. Inbound dedupe: partial unique index `uq_peppol_message_id` on `message_id WHERE message_id IS NOT NULL`. See `docs/peppol.md`
   - `Contract` — vendor contract / CLM spine. contract_number, contract_type (purchase/service/subscription/lease/sla/msa/sow/other), status (draft/active/expired/terminated/cancelled), vendor_id, money (`Numeric` total_value / spend_limit + not_to_exceed), lifecycle dates, renewal config (auto_renew, renewal_notice_days, renewal_alert_sent_at), terms (JSONB), file_key. Spend link is `Invoice.contract_id`. See `docs/contracts.md`
   - `ContractLineItem` — contract_id, line_number, item_code, description, quantity, unit_price, total, gl_account

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
| `services/contract_renewal.py` | Contract renewal-alert sweep. Sweeps every tenant DB; finds `active` contracts within their own `renewal_notice_days` of `end_date` with no alert sent, notifies the owner + AP managers once (`contract_renewal_due` event), then stamps `renewal_alert_sent_at` for idempotency (cleared on `POST /api/contracts/{id}/renew`). Disabled by default (`AP_CONTRACT_RENEWAL_ENABLED`); `AP_CONTRACT_RENEWAL_INTERVAL_SECONDS` / `_DEFAULT_NOTICE_DAYS`. See `docs/contracts.md`. |
| `services/discount_auto_trigger.py` | Dynamic-discounting auto-capture sweep. Sweeps every tenant DB; auto-accepts `offered` `DiscountOffer`s whose annualized ROI clears `AP_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD`, writing a `discount_offer.auto_accepted` audit row. **Only flags `offered → accepted` — never creates a Payment/PaymentRun**; the status guard is the dedupe. Disabled by default (`AP_DISCOUNT_OPTIMIZATION_ENABLED`). See `docs/dynamic-discounting.md`. |
| `services/retention_sweep.py` | Retention-policy enforcement sweep (SOX records management). Sweeps every tenant DB; soft-archives overdue terminal (`done`/`paid`) invoices via a `meta.archived_at` marker (idempotent — re-run never double-archives) and writes a `retention.archived` manifest. **Composes with the audit-immutability trigger — NEVER deletes `audit_log` rows**; for the audit class "retention" verifies WORM shipment (`shipped_at`) + records overdue/unshipped counts only. Windows are per-class on `Organization.settings.retention` (`resolve_retention_months`); `GET/PUT /api/retention-policy` reads/updates them. Disabled by default (`AP_RETENTION_ENABLED`); `AP_RETENTION_INTERVAL_SECONDS` / `_DEFAULT_MONTHS`. See `docs/retention.md`. |
| `services/recurring_invoices.py` | Recurring / subscription invoice generation sweep. Sweeps every tenant DB; finds `active` `RecurringInvoiceTemplate`s whose `next_run_on` has arrived, generates the next pre-coded `Invoice` into the approval queue (period_key `YYYY-MM` / `YYYY-Qn` / `YYYY`), advances `next_run_on`, and writes a `recurring_template.generated` audit row. **Idempotent on `(template, period_key)`** via the partial unique index `uq_invoice_recurring_period` (a double-fire never double-creates); **only creates an Invoice in the queue — never creates a Payment/PaymentRun**, exactly like `discount_auto_trigger`. Per-tenant cap `AP_RECURRING_INVOICES_MAX_PER_SWEEP`. Disabled by default (`AP_RECURRING_INVOICES_ENABLED`); `AP_RECURRING_INVOICES_INTERVAL_SECONDS`. See `docs/recurring-invoices.md`. |
| `services/scheduled_reports.py` | Scheduled-report runner. `run_scheduled_reports_once` sweeps every tenant DB; runs each `enabled` schedule whose `next_run_at` has arrived (`execute_schedule`: generate CSV via `report_export` → email recipients → bump `next_run_at` by the cadence / persist a `[retry N]` failure marker, auto-disabling after 5 consecutive failures). One tenant's failure never halts the sweep. Disabled by default (`AP_SCHEDULED_REPORTS_ENABLED`); `AP_SCHEDULED_REPORTS_TICK_SECONDS`. See `docs/analytics.md` § Scheduled report delivery. |

These long-lived asyncio tasks are started in `main.lifespan` (each behind its `AP_*_ENABLED` gate) and cancelled on shutdown.

## Adapter patterns

### Extraction adapters (`services/extraction_adapters/`)

```python
@register_extraction_adapter("my_provider")
class MyAdapter(ExtractionAdapter):
    async def extract(self, file_bytes, file_key, mime_type, file_url) -> ExtractionResult: ...
    async def test_connection(self) -> bool: ...
```

Registered: `claude_vision`, `openai_vision`, `aws_textract`, `ollama`, `einvoice`, `mock`

`ExtractionResult` contains per-field `ExtractedField(value, confidence)` + `line_items` + `overall_confidence`.

**Two program types**: `platform` (app-level Claude Vision key, usage tracked) vs `byok` (customer provides own API key).

**Structured e-invoices** (`einvoice`): the `app/services/e_invoice/` package parses UBL 2.1 / UN-CEFACT CII / Factur-X·ZUGFeRD (embedded CII in a PDF/A-3) into a normalized `EInvoiceDocument` — pure, local, XXE-hardened lxml, no LLM/network. Routing is **not** config-driven: `extraction.run_extraction` is the single choke point both upload and email-intake reach, and it calls `_detect_structured_format(file_bytes, file_key)` right after the S3 fetch — a structured file overrides `config.provider` to `einvoice` and passes the real mime; everything else falls through to the configured vision/mock adapter. Confidence 1.0 on every present field → auto-approve; malformed → field-named `EInvoiceValidationError` (no PII). The same package also generates **outbound** XML from the same normalized model: `generate.generate_ubl(doc) -> bytes` (UBL 2.1, the exact inverse of `ubl.py`; round-trip `parse_ubl(generate_ubl(doc)) == doc`) and `generate_cii.generate_cii(doc) -> bytes` (UN/CEFACT CII D16B, the exact inverse of `cii.py`; round-trip `parse_cii(generate_cii(doc)) == doc`; the Factur-X/ZUGFeRD dialect), `mapper.invoice_to_einvoice_document(invoice, line_items, BuyerIdentity)` (ORM → normalized model), and `tax_rules.py` — the shared country tax-validation building block (per-country VAT/GST/IVA tax-ID format + rate plausibility + reverse-charge/zero-rate, PII-free `FieldError`s) wired into both inbound `validate_document(check_tax=True)` and the outbound export guard. Routes: `GET /api/invoices/{id}/einvoice?format=ubl|cii|fatturapa|cfdi|nfe|dian` (role-gated AP export, 422 on tax-invalid; `ubl`/`cii` are built-in dialects sharing the tax guard, the rest national formats) + `GET /portal/invoices/{id}/einvoice` (vendor-scoped supplier download, never 422s the supplier). See `docs/e-invoicing.md`.

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

The three real adapters' provider base URLs are env-overridable via the
operator-trusted `AP_ERP_MERGE_API_BASE` / `AP_ERP_NETSUITE_API_BASE` /
`AP_ERP_D365_API_BASE` / `AP_ERP_D365_TOKEN_URL` (process-level, so they bypass
the admin-config SSRF guard; an admin-supplied `base_url` stays guarded).
`backend/.env.development` points all four at the local fake ERP server — the
`fake-erp` compose service (opt-in `erp` profile, :12112, built from
`tools/fake-erp/`, deterministic PO/GL fixtures, shape-checked auth only) — so
`pnpm erp:up` → `pnpm test:erp` exercises `merge_dev`/`netsuite`/
`dynamics_365_bc` end-to-end with no cloud account. See
`docs/erp-integration.md` § Local e2e testing (fake ERP server).

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

### Positive Pay formatters (`services/positive_pay_adapters/`)

```python
@register_positive_pay_formatter("my_bank")
class MyBankFormatter(PositivePayFormatter):
    format_name = "my_bank"
    file_extension = "csv"
    content_type = "text/csv"
    def format_check_issue(self, items: list[CheckIssueItem], ctx: FormatterContext) -> str: ...
    def format_ach_authorization(self, items: list[AchAuthorizationItem], ctx: FormatterContext) -> str: ...
```

Registered: `csv` (default), `fixed_width`. `get_positive_pay_formatter(name)` defaults to `csv` and falls back to `csv` on an unknown key (never raises — a bad config can't break generation). Renders a Positive Pay fraud-control file from the formatter dataclasses (`CheckIssueItem` / `AchAuthorizationItem` / `FormatterContext` in `base.py`); the async DB→dataclass builders + the pure return classifier (`matched_ok` / `amount_mismatch` / `not_on_file`) live in `services/positive_pay.py`. The rendered file legitimately holds full account/routing numbers and is stored in MinIO via `storage.upload_positive_pay_file`; the `PositivePayFile` DB row + audit/logs/errors are PII-free (`account_last4` only). Mounted at `/api/positive-pay`. Idempotent per `(payment_run_id, bank_format)` via the partial unique index `uq_positive_pay_run_format`. See `docs/positive-pay.md`.

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

### Supplier-financing adapters (`services/financing_adapters/`)

```python
@register_financing_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"
    def __init__(self, config: dict | None = None): ...
    async def quote(self, *, invoice_amount, currency, due_date, vendor_name,
                    vendor_country=None) -> FinancingQuote: ...
    async def request_funding(self, *, quote, idempotency_key) -> FinancingFundingResult: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock` (local-first default — deterministic, no network/credential), `c2fo` (skeleton — live key required, fail-closed). A supply-chain-finance marketplace funds a supplier's early invoice payment (advance = face − fee); the buyer repays at the net due date. Selected per-org via `Organization.settings.financing.provider`. See `docs/dynamic-discounting.md`.

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

### Vendor-enrichment adapters (`services/enrichment_adapters/`)

```python
@register_enrichment_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"
    def __init__(self, config: dict | None = None): ...
    async def enrich_vendor(self, query: VendorEnrichmentQuery) -> VendorFirmographics: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock` (deterministic synthetic firmographics, no network/credential — the local-first default), `dun_bradstreet` + `clearbit` (httpx skeletons — live key via per-org settings; **fail closed** `EnrichmentNotConfigured` without it, no hardcoded fallback). `get_enrichment_adapter(config)` resolves `Organization.settings.enrichment.provider` → `AP_VENDOR_ENRICHMENT_PROVIDER` (default `mock`); an unknown name falls back to `mock`. External vendor firmographics (legal name / registered address / industry+SIC/NAICS / employee count / revenue / website / DUNS / founding year) for `POST /api/enrichment/vendors/{id}/enrich`. **Advisory / suggestion-only** — returns the firmographics + a per-field suggestion diff but NEVER writes back onto the `Vendor` row. Raw `tax_id` is an input match-key only — never echoed (only `***<last4>` via `mask_tax_id`), never logged. See `docs/data-enrichment.md` § External enrichment.

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

Registry by `exception_type` (`@register_exception_agent`). The `coordinator.run_agent` dispatches by exception type, gates auto-resolve on the org's `autonomy_level` → confidence threshold, and writes an append-only `AgentDecision` row every run; auto-resolves also write the DB-immutable `invoice.approved` audit row via `review.approve_invoice`. `po_mismatch` is owned by a single registered **dispatcher** (`resolvers/po_mismatch.py`) that delegates to three real resolvers, disjoint: `amount_mismatch_v1` (status `matched` — amount variance, snap to PO total + approve), `missing_po_v1` (status `no_po`, exactly one PO matching the full amount — find the real PO by vendor + amount + date, link by `po_number`, approve; never adjusts the amount), and `multi_po_split_v1` (status `no_po`, no single PO matching but a **unique** PO set summing to the total within tolerance — a consolidated invoice spanning several POs; links the whole set via a combined `po_number` ref + multi-PO `po_match` snapshot, approves; never adjusts the amount; bounded combinatorial search ≤12 candidates / set-size ≤4, over-cap pool escalates, ambiguous/none escalates). `missing_data` is owned by a second dispatcher delegating to `gl_coding_v1` (fill/correct the GL — and an empty cost center — from the vendor's dominant approved history via the pure `vendor_enrichment.suggest_fields`, then approve through `review.approve_invoice(corrections=…)`; never moves money). Plus escalate-only stubs for `duplicate`, `fraud_flag`. Local-first: the optional LLM rationale fails soft to a deterministic template with no key. See `docs/exception-agents.md`.

### PEPPOL adapters (`services/peppol_adapters/`)

```python
@register_peppol_adapter("my_ap")
class MyAdapter(PeppolAdapter):
    async def resolve_participant(self, pid: ParticipantId) -> ParticipantCapability: ...
    async def send(self, request: TransmissionRequest) -> TransmissionResult: ...
    async def test_connection(self) -> bool: ...
    def parse_inbound(self, headers, body) -> InboundPeppolMessage | None: ...
```

Registered: `mock` (in-process, no network — the **local-first default**), `as4_gateway` (real — `httpx` to a hosted Access Point; key via sops, no hardcoded fallback). Selection via `Organization.settings.peppol.provider` → `AP_PEPPOL_PROVIDER` (default `mock`). Outbound **send** turns an invoice into UBL via the `e_invoice` package, resolves the receiver via SMP/SML (`resolve_participant`), and transmits via the gateway; SBDH wrapping lives in the adapter, never the generator. `services/peppol_send.send_invoice_over_peppol` orchestrates it (map → tax-validate → UBL → resolve → INSERT `peppol_transmissions('sending')` → send → audit), idempotent at the DB layer. Route `POST /api/invoices/{id}/peppol-send`.

**Inbound receive** (the C4 corner) is now implemented: `parse_inbound` is real on both adapters (mock parses a dev JSON/header envelope; `as4_gateway` maps the hosted AP's inbound-delivery envelope). `api/peppol_inbound.public_router` mounts `POST /api/peppol/inbound/{tenant_slug}` (public-by-design, HMAC-gated, tenant in path, always 204). `services/peppol_receive.receive_peppol_message` mirrors `email_intake.process_inbound_email`: dedupe-precheck → `e_invoice.parse_e_invoice` (structural validate) → create `Invoice(status=new)` → claim the `uq_peppol_message_id` slot with a `PeppolTransmission(direction="inbound", status="delivered")` flushed **before** the S3 upload (so a concurrent-redelivery loser's `IntegrityError` rolls back the whole tenant txn — no second invoice, no orphaned S3 object) → upload payload → `invoice.peppol_received` audit → commit → `dispatch_extraction` (auto-routes to the `einvoice` adapter). Dedupe is the DB unique index only (deliberately **not** Redis — a 24h TTL would let a later redelivery slip through). See `docs/peppol.md`.

### Punch-out adapters (`services/punchout_adapters/`)

```python
@register_punchout_adapter("my_provider")
class MyAdapter(PunchoutAdapter):
    def build_setup_request(self, ctx: PunchoutSetupContext) -> PunchoutStartResult: ...
    def parse_order_message(self, headers, body: bytes) -> PunchoutCart | None: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock` (in-process, no supplier/network — the **local-first
default**), `cxml` (real cXML build/parse; supplier shared secret via sops, **no
hardcoded fallback** → fails closed `punchout_not_configured`; OCI shape behind
the same interface via `protocol="oci"`). Selection via
`Organization.settings.punchout.provider` → `AP_PUNCHOUT_PROVIDER` (default
`mock`). Live cXML/OCI catalog punch-out: a `punchout` `Catalog` starts a
`PunchoutSession` (migration `0045`) → adapter builds a PunchOutSetupRequest +
returns a supplier start URL → the supplier POSTs a PunchOutOrderMessage cart to
the **public** secret-gated return endpoint (`POST
/api/catalogs/punchout/return/{tenant_slug}`, HMAC + BuyerCookie gated, always
204 on rejection — mirrors PEPPOL inbound) → the buyer converts the returned
cart into a `PurchaseRequisition` (idempotent + row-locked, reusing
`requisition_service` primitives). `services/catalog_service.py` orchestrates;
cXML build/parse lives in `services/punchout_adapters/cxml.py` (XXE-hardened
parse reused from `e_invoice/_xml`). See `docs/procurement-catalogs.md`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_PUNCHOUT_PROVIDER` | `mock` | Adapter — `mock` \| `cxml`. Per-org override `Organization.settings.punchout.provider`. |
| `AP_PUNCHOUT_SHARED_SECRET` | (empty) | cXML supplier credential — no hardcoded fallback; sops in deployed. |
| `AP_PUNCHOUT_RETURN_SIGNING_SECRET` | (empty) | HMAC key the supplier signs the cart-return POST with. No hardcoded fallback; committed `.env.development` sets a NON-secret dev value. |
| `AP_PUNCHOUT_RETURN_MAX_BYTES` | `4194304` | Cart-return body cap (memory-exhaustion guard). |

### Billing adapters (`services/billing_adapters/`)

```python
@register_billing_adapter("my_provider")
class MyAdapter(BillingAdapter):
    async def create_subscription(self, request: CreateSubscriptionRequest) -> ProviderSubscription: ...
    async def get_subscription(self, external_subscription_id: str) -> ProviderSubscription: ...
    async def list_invoices(self, *, customer_id, limit=24) -> list[ProviderInvoice]: ...
    async def report_usage(self, report: UsageReport) -> None: ...
    async def create_setup_intent(self, customer_id) -> ProviderSetupIntent | None: ...
    async def list_payment_methods(self, customer_id) -> list[ProviderPaymentMethod]: ...
    def parse_webhook(self, headers: dict, body: bytes) -> BillingWebhookEvent | None: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock` (in-process, deterministic, no network/credential — the
**local-first default**), `stripe_billing` (live REST over `httpx` — key via
sops, **fails closed** `BillingNotConfigured` without it). Implemented:
`ensure_customer` / `ensure_price` (per-org customer + per-plan recurring price,
idempotent creates, minor-units via exact Decimal), `create_subscription` /
`get_subscription`, `list_invoices` (the org's past invoices/receipts as
`ProviderInvoice` DTOs — money as exact decimal string; base supplies a safe
`[]` default, mock fabricates deterministic receipts, Stripe GETs
`/v1/invoices`), `report_usage` (one Billing Meter Event per meter, exact
decimal-string quantities), and `parse_webhook` (Stripe-Signature HMAC verify).
Selection via `Organization.settings.billing.provider` → `AP_BILLING_PROVIDER`
(default `mock`). This is the AP platform's OWN customer billing (plans /
subscriptions / metering — control-plane, keyed by org), distinct from the AP
money path the app runs for customers. The **payment-method** capability
(`create_setup_intent` → `ProviderSetupIntent` with a single-use `client_secret`,
`list_payment_methods` → `ProviderPaymentMethod` PII-safe metadata only —
brand/last4/exp, **never a PAN**) is also implemented: base supplies safe
defaults (`None` / `[]`), mock returns a deterministic SetupIntent + a
deterministic `visa ****4242`, Stripe POSTs `/v1/setup_intents` + GETs
`/v1/payment_methods?type=card` (fails closed without a key). Usage rollup off the existing
`extraction_usage` / `card_rebates` meters lives in
`services/billing/usage_rollup.py`; entitlement gating (`require_entitlement` /
`require_api_entitlement` in `deps.py`, 402 on a plan miss) reads
`services/billing/entitlements.py`. Per-org customer/price provisioning
(`services/billing/provisioning.py` → `settings.billing.stripe_customer_id` +
`.plan_price_ids`, no migration), mid-period proration
(`services/billing/proration.py`, pure Decimal, `ROUND_HALF_UP` 2 dp), and the
plan-change endpoint (`POST /api/billing/change-plan`, admin/cfo, idempotent +
audited), and the invoices/receipts list endpoint (`GET /api/billing/invoices`,
admin/cfo, money as exact strings, graceful empty-list on no-customer /
unconfigured), and the payment-method endpoint (`POST
/api/billing/payment-method/setup-intent` + `GET /api/billing/payment-methods`,
admin/cfo, PII-safe card metadata only, graceful not-configured / empty on
no-customer / unconfigured) are shipped; the invoices/receipts + payment-method
UI ships on `/billing` (`frontend/src/routes/billing/` — saved-cards list +
add/replace-card SetupIntent flow with a deployed-only Stripe Elements seam),
only the live-Stripe plan-change UI is later. See `docs/billing.md`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_BILLING_PROVIDER` | `mock` | Billing adapter — `mock` \| `stripe_billing`. Per-org override `Organization.settings.billing.provider`. |
| `AP_BILLING_STRIPE_API_KEY` | (empty) | Live Stripe Billing key — no hardcoded fallback; sops in deployed. Adapter fails closed without it. |
| `AP_BILLING_STRIPE_WEBHOOK_SECRET` | (empty) | HMAC secret for Stripe webhook verification — no fallback; sops in deployed. |

## Webhook security (`services/webhook_security.py`)

Every inbound webhook handler — payments, cards, ERP, email-intake, PEPPOL inbound — verifies the provider's HMAC over the raw request body and dedupes (by event id, or — for PEPPOL inbound — by the AS4 MessageId at the DB layer) before mutating state (project invariant #9). Shared helpers:

- `verify_hmac_sha256(secret, raw_body, provided_hex)` — constant-time HMAC-SHA256 check via `hmac.compare_digest`. Empty / missing secret or signature fail closed.
- `is_event_already_processed(provider, event_id, ttl_seconds=86400)` — Redis `SET NX EX` dedup. First delivery returns `False`; replays within the TTL window return `True` so the handler short-circuits.
- `extract_signature_header(headers, *candidates)` — case-insensitive multi-candidate header lookup (different providers use different header names).

Per-tenant secrets:

| Endpoint | Settings path |
|---|---|
| `/api/payments/webhook/...` | `Organization.settings.payments.webhook_secret` (verified inside the adapter's `parse_webhook`). The route rejects `provider == "mock"` outright before any tenant lookup — the `mock` adapter's `parse_webhook` does no signature verification and `mock` is the default provider for un-configured tenants, so serving it publicly would accept forged status transitions (mock never delivers real webhooks). Mirrors `cards.card_webhook`'s `lithic`/`nium` allowlist and the billing route's boot-time mock refusal. |
| `/api/cards/webhook/{provider}` | `Organization.settings.cards.webhook_signing_secret` |
| `/api/erp/webhook/{erp_type}` | `Organization.settings.erp.webhook_signing_secret` |
| `/api/email-intake/inbound/{provider}` | `AP_EMAIL_INTAKE_SIGNING_SECRET` (process-level HMAC key; verified in `email_intake.verify_signature`). Dedupe is `is_event_already_processed("email_intake", message_id)`, claimed right after tenant resolution and released via `release_event_claim` if invoice creation fails downstream (mirrors `api/cards.py`'s claim/release discipline) so a redelivery can retry. Recipient-token match uses `hmac.compare_digest`. |
| `/api/peppol/inbound/{tenant_slug}` | `AP_PEPPOL_INBOUND_SIGNING_SECRET` (process-level HMAC key; verified by `peppol_receive.verify_inbound_signature`). Dedupe is the DB `uq_peppol_message_id` index, not Redis. |
| `/api/catalogs/punchout/return/{tenant_slug}` | `AP_PUNCHOUT_RETURN_SIGNING_SECRET` (process-level HMAC key; verified in `catalogs._verify_return_signature`). Correlation is the BuyerCookie matched to a pending `PunchoutSession`. |
| `/api/approvals/slack/interactivity` | `AP_SLACK_SIGNING_SECRET` (process-level HMAC key; verified in `slack_approvals._verify_slack_signature` over `v0:{X-Slack-Request-Timestamp}:{raw_body}`, with a `±AP_SLACK_REQUEST_MAX_AGE_SECONDS` replay window). Per-action dedupe is the single-use action-token `jti` in Redis (the email-approval mechanism), not `is_event_already_processed`. Returns an opaque 200 ack (Slack-friendly) on every path, not 204. See `docs/slack-approval.md`. |

Every webhook handler returns **204 silently** on every rejection path (bad signature, unknown tenant, missing event id, unknown card / invoice / payment, disabled master switch, unparseable / malformed inbound document) — except the Slack interactivity webhook, which returns an opaque **200 ack** on every path (Slack requires 2xx to acknowledge a button click; the ack text is identical across success and rejection, so it doesn't enumerate), and the email-intake webhook, which is a hybrid: pre-signature rejections (unknown provider / bad signature / unparseable body — nothing sensitive resolved yet) still return 204, but every outcome *after* the signature verifies (unknown/disabled intake token, duplicate delivery, no usable attachments, a processing exception, or genuine success) returns the SAME opaque 200 ack (`email_intake._ack()`) — because unlike the other handlers, a 204-vs-200-with-a-body split there would itself be the token-enumeration oracle (the platform-wide signing secret is shared across all tenants, so anyone who can sign a request can watch for the response to change once they guess a valid intake token). Distinct 4xx responses / differing response bodies would enumerate tenant slugs or card/intake tokens. Tests: `backend/tests/test_webhook_security.py`, `tests/test_payment_webhook_security.py`, `tests/test_peppol_inbound.py`, `tests/test_slack_approvals.py`, `tests/test_email_intake.py`, `tests/test_email_intake_processing.py`.

## Security utilities

- **Passwords**: a single shared `pwd_context` in `app/utils/passwords.py` uses `bcrypt_sha256` (SHA-256 pre-hash → bcrypt) to side-step bcrypt's 72-byte truncation. Every call site (auth, admin, portal, vendors, tenant_provisioning, scripts/seed) imports from there — never construct a fresh `CryptContext`. Complexity rules in `validate_password_complexity` (min 12 chars, upper/lower/digit).
- **Filename sanitiser**: `app/services/storage.py::_safe_filename` strips path separators, `..`, leading dots (no dotfiles), and control / non-printable characters. Used by `upload_invoice_file` before interpolating the filename into the S3 key. Without it, a vendor portal POST with filename `../../other-org/secret.pdf` could land under another tenant's prefix.
- **File download cross-tenant check**: `GET /api/workflow/file/{file_key:path}` verifies the key's first segment equals the requesting user's `organization_id`. Same 404 for wrong-org and missing-file so the response doesn't enumerate prefixes.

## Audit immutability + access auditing (SOX)

The `audit_log` table is **append-only at the database level**: migration `0022_sox_audit_immutable` installs `BEFORE` triggers (DDL in `app/services/audit_immutability.py`) that reject every DELETE and every UPDATE touching a column other than `shipped_at`. The `shipped_at` carve-out lets `audit_log_shipper.py` stamp shipped rows; everything else is frozen, so a rogue ORM call or a direct `psql` session can't tamper with the trail. Installed on every tenant DB — migration fan-out for existing tenants, `tenant_provisioning._create_tenant_tables` for fresh ones (which use `create_all`, not Alembic). See `docs/audit-log-shipping.md`.

Two request-path helpers in `app/services/audit_access.py` (thin wrappers over `dispatch_audit`, not reimplementations):
- `log_access(...)` — writes a `<entity_type>.viewed` row for SOX access-control auditing. Instrumented reads: vendor detail (`vendor.viewed`), payment detail (`payment.viewed`), card PAN reveal (`card.details_viewed`), the audit-trail view (`audit.viewed`), and every auditor export (`audit.exported`). The `details` payload records the field-**names** accessed, never the values — no tax id / bank number / PAN ever enters the audit trail (PII-out-of-logs).
- `build_field_diff(before, after, fields)` — produces `{field: {old, new}}` for SOX change history on invoice edits + approve-with-corrections. Money serialises as **string-Decimal**, never float.

The auditor-export surface is `app/api/audit.py` (`/api/audit/export`, `/api/audit/invoice/{id}` — GET-only, admin/CFO). `/api/audit/export` also serves a formatted **PDF** SOX audit-trail report via `?format=pdf` (cover + event-count summary + chronological table; `app/services/audit_report_pdf.py`, pure-function modelled on `remittance_pdf.py`; renders only the field-NAME-sanitised entries). See `docs/api-reference.md` § Audit Trail.

**Periodic access reviews (SOX)** — `app/api/access_reviews.py` (`GET /api/access-reviews` + `POST /api/access-reviews/acknowledge`, admin/CFO). Compute-on-read (no migration): `app/services/access_review.py` flags users holding an elevated role (`admin`/`ap_manager`/`cfo`) whose last *mutating* audit action is older than `AP_ACCESS_REVIEW_DORMANT_DAYS` (default 90), or who never acted, as DORMANT. The review list is itself a sensitive read (`access_review.viewed`); acknowledge writes `access_review.completed` + stamps `Organization.settings.access_review`. See `docs/access-reviews.md`.

**Digital signatures on approvals (non-repudiation):** every `invoice.approved` audit row carries an HMAC-SHA256 "timestamp + user hash" in `details.signature` over the canonical approval facts (invoice id + exact Decimal amount + actor + decision + timestamp). Signed in `services/review.approve_invoice` (`services/approval_signature.py` — pure); re-verifiable at `GET /api/audit/invoice/{id}/verify-signatures` (admin/CFO), where a post-approval tamper of the amount/actor/timestamp → `valid: false`. Key `AP_APPROVAL_SIGNING_KEY` — empty → signing skipped, NON-secret committed dev value, real key via sops (no hardcoded fallback). See `docs/approval-signatures.md`.

**Retention policies (records management):** per-record-class windows on `Organization.settings.retention` (`GET/PUT /api/retention-policy`, admin); the `retention_sweep` background loop archives overdue terminal invoices via a `meta.archived_at` marker and, for the WORM `audit_log` class, verifies shipment instead of deleting — it never deletes audit rows (composes with the immutability trigger). See `docs/retention.md`.

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

### MFA (`services/mfa.py`, `services/webauthn.py`)

- TOTP (pyotp) + email-OTP backup + **WebAuthn/passkeys** (`py_webauthn`). Master switch `AP_MFA_ENABLED` (default `false` for local dev) gates all three.
- Per-user TOTP secret on `User.mfa_secret`; org-wide enforcement via `Organization.settings.mfa.required`.
- **Passkeys are a separate code path** (`services/webauthn.py`), additive + opt-in. Credentials live in the control-plane `webauthn_credentials` table (`WebAuthnCredential`, migration 0063, in `CONTROL_TABLES`) — one row per registered authenticator, keyed by `user_id`. Register/list/delete + authenticate endpoints under `/api/auth/mfa/passkey/*`; the authenticate ceremony is gated by the login-issued MFA challenge token (public, pre-access-token), register/list/delete require JWT. The per-ceremony challenge is stashed single-use in Redis (`webauthn:{reg,auth}_challenge:<user_id>`); the signature counter is verified + bumped (clone-detection). RP ID / origins configurable (`AP_WEBAUTHN_RP_ID` / `AP_WEBAUTHN_ORIGINS`; dev defaults `localhost` / `http://localhost:7777`). Public key + counter are not secret in the password sense and never logged.
- Login returns either `TokenResponse` or `MFAChallengeResponse`. Challenge token is a short-lived JWT with `typ: mfa_challenge` — verified at `POST /api/auth/mfa/verify` (totp/email) or the passkey authenticate endpoints. `methods` lists the offered factors (`totp` / `passkey` / `email`); a passkey-only user trips the gate.
- Email-OTP hashes live in Redis (`mfa:email_otp:<user_id>`), short TTL, single-use.
- SSO sign-in skips our MFA challenge — IdPs handle their own MFA.
- Full reference: `../docs/authentication.md` § MFA + § Passkeys.

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
  "payments": { "provider", "credentials": { ... }, "webhook_secret": "...", "cfo_approval_above": Decimal,
                "require_run_segregation": true },
  "mfa": { "required": true|false },
  "chat_notifications": { "enabled": true|false, "provider": "slack"|"teams"|"mock",
                          "webhook_url": "...", "events": { "invoice_approved": true, ... } },
  "sso": { ... },
  "fraud_rules": { ... },
  "exception_agents": { "autonomy_level": "conservative"|"balanced"|"aggressive", "amount_tolerance_pct": 2.5 }
}
```

The three `webhook_*_secret` fields are HMAC keys used by the inbound webhook handlers — see "Webhook security" above.

## Exception types

`duplicate`, `po_mismatch`, `fraud_flag`, `extraction_failed`, `unverified_vendor`, `review_rejected`, `amount_exceeded`, `missing_data`, `quality_hold`, `contract_noncompliant`, `erp_reconciliation`

Severity: `error`, `warning`, `info`. Auto-detected by `invoice_warnings.py`. `erp_reconciliation` is opened by the ERP webhook (`api/erp_webhook.py`) when the ERP reports an invoice VOIDED/CANCELLED that we already advanced past the point where `→ failed` is a legal transition (`sent_to_erp` / `posted_in_erp` / `payment_scheduled` / `paid`) — money may be in flight, so it is flagged for human reconciliation instead of auto-transitioned (idempotent per open exception, PII-free description).

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/seed.py` | Creates 2 tenants (acme, techflow) with full sample data (vendors, invoices, POs, payments, exceptions) + a `WorkflowInstance`/`WorkflowStep` per invoice (so the approval queue + assistant pending-approvals tool aren't empty — `ready_for_review` invoices get an active approval step assigned to the org admin) + calls `seed_extras` so contracts / credit memos / discount offers / expenses are populated too |
| `scripts/seed_extras.py` | Additive, idempotent per-tenant seed for the contract (`/contracts`), credit-memo (`/credit-memos`), discounting (`/discounts`) and expense (`/expenses`) pages. `seed_extras(session, org_id)` is reused in-line by `seed_tenant`; the CLI (`--tenant ap_acme`) tops up an already-seeded tenant without a wipe. Skips if the tenant already has contracts. |
| `scripts/seed_payable_invoices.py` | Tops up a tenant's payment queue with N approved invoices (`--tenant`, `--count`) — re-run after executing a payment run drains the queue. |
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
