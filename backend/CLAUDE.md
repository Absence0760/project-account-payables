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
| AI Cash-Flow Copilot (Phases 1–2 — read-only cash tools + proposed plans + `/api/cash-flow` façade) | `../docs/cash-flow-copilot.md` (repo-root `docs/`) |
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
| Line-total reconciliation (lines vs the header amount) | `docs/line-total-reconciliation.md` |
| Vendor management | `docs/vendor-management.md` |
| Local AI testing (Ollama) | `docs/local-ai-testing.md` |
| Docker Compose services | `docs/docker.md` |
| Redis | `docs/redis.md` |
| MinIO / S3 | `docs/minio.md` |
| Audit-log shipping (SOC 2) | `docs/audit-log-shipping.md` |
| Background sweeps — health, supervision, the shared loop runner | `docs/background-sweeps.md` |
| Periodic access reviews (SOX) | `docs/access-reviews.md` |
| Audit-log summarization (invoice modal) | `docs/audit-summary.md` |
| Email + in-app notifications | `docs/notifications.md` |
| Email approval (approve/reject from the email, no login) | `docs/email-approval.md` |
| Slack interactive approval (approve/reject from Slack buttons, no login) | `docs/slack-approval.md` |
| Exception queue lifecycle + its append-only audit trail | `docs/exception-lifecycle.md` |
| Exception agents (autonomous resolution) | `docs/exception-agents.md` |
| Adaptive AI workflows | `docs/adaptive-workflows.md` |
| Data enrichment (auto-fill, price variance, vendor scoring) | `docs/data-enrichment.md` |
| PEPPOL AS4 outbound (e-invoice transmission) | `docs/peppol.md` |
| Contract management (CLM) | `docs/contracts.md` |
| Expense management (incl. multi-currency locked-FX reports) | `docs/expense-management.md` |
| Digital signatures on approvals (SOX) | `docs/approval-signatures.md` |
| Retention policies (SOX records management) | `docs/retention.md` |
| Privacy — GDPR/CCPA DSAR export + right-to-erasure | `docs/privacy.md` |
| Public Developer API (API keys + `/api/v1`) | `docs/public-api.md` |
| Platform billing & metering (plans / subscriptions / entitlements) | `docs/billing.md` |
| 1099 tracking + the card-rail (1099-K) exclusion | `docs/tax-1099.md` |

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
FEOH_MIGRATE_TENANT=feoh_acme alembic upgrade head      # single tenant
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
| `requirements.lock` | base runtime only (no extras) | `backend/Dockerfile` — `uv pip install --system --no-cache --require-hashes …` (app runs from source, no editable install) |

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
the regenerated locks in the same change as the `pyproject.toml` edit.

**A stale lock does not fail the install** — that's the trap. The lock is
internally consistent, so `--require-hashes` succeeds and CI stays green
while the image installs whatever the lock says, ignoring the floor you
just raised in `pyproject.toml`. This is not hypothetical: five merged
Dependabot PRs (#111, #113, #114, #115, #117) raised `uvicorn`, `boto3`,
`pgvector`, `joserfc` and `ruff` in `pyproject.toml`, and the image kept
shipping every pre-bump version — including the security-motivated ones —
until it was caught.

Dependabot cannot close this itself. Its pip-compile support only pairs an
`.in` file with a lockfile ending in `.txt`; these locks are compiled from
`pyproject.toml` under a `.lock` name, so Dependabot updates the manifest
and never touches them. (`tools/fake-erp` uses the `.in`/`.txt` pair
precisely so Dependabot *can* maintain it there.)

**Nothing closes this gap automatically — every Dependabot pip PR needs the
locks regenerated by hand with the commands above.** A
`.github/workflows/dependabot-lockfile.yml` used to do it, but it was removed
in #325 because its push job gated on a `DEPENDABOT_LOCKFILE_PAT` Dependabot
secret that has never been set in this estate, and the gate treated the
missing PAT as skip-and-succeed — so a completely inert workflow looked
identical to a working one. The removal was right; it just left the manual
step as the whole process. See
[docs/followups.md](../docs/followups.md) § (b) Operator steps on merged code
for the restore-it-properly option and why it is optional.

The cost is real but small and it lands in batches: six pip PRs (#334, #335,
#337, #339, #346, #347) sat red for a week on exactly this before being
regenerated in one pass. Prefer that — bump every open floor, recompile once,
and let Dependabot close the superseded PRs — over one lock commit per bump.

`tests/test_dependency_lock_sync.py` is the guard: it checks each declared
requirement against the version its lock pins, so a manifest bump without a
regenerated lock fails loudly. It compares constraints to pins — it never
re-resolves and never hits the network, so it can't go red just because a
new release appeared on PyPI overnight.

### `.dockerignore` — what does NOT enter the image

`backend/Dockerfile` ends in `COPY . .`, so `backend/.dockerignore` is the
only thing standing between your working tree and a shipped layer. It
excludes, in order of how much they matter:

1. **`.env` / `.env.*`** (except the committed `.env.development`) and
   `*.sops` — a gitignored local `.env` holds real credentials, and an
   image layer is readable by anyone who can pull it.
2. **`.venv`** — the local dev virtualenv. Copied in, it lands at
   `/app/.venv` as a second Python install on whatever versions that
   laptop had; Trivy then reports CVEs against packages the image never
   runs (this is exactly how a stale `pip` showed up in a scan).
3. Tool caches, `tests/`, `docs/`, egg-info — pure layer weight.

Excluding the venv and the uv download cache took the image from **1.26 GB
to 649 MB**. If you add something the running container genuinely needs,
check it isn't caught by a pattern there — `app/`, `alembic/`,
`alembic.ini`, `main.py`, `pyproject.toml`, `requirements.lock` and
`scripts/` are the deliberate keeps (`deploy/deploy.sh` and
`deploy/add-tenant.sh` run `scripts/*.py` inside this image).

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

## Test databases (the `realdb` harness)

Most of the suite is mock-based. Tests that request the `realdb` fixture
(`tests/conftest.py`) run against a live Postgres and a **pair of real tenant
databases** — the only way to prove cross-tenant isolation, the SQL filters, and
commit durability. Before each such test the harness truncates the tenant
business tables, restores the Default entity, resets the shared control-plane
`Organization` row, and reaps every other backend on those databases.

**The tenant pair is exclusive to one pytest process.** Because the reset both
TRUNCATEs and `pg_terminate_backend`s, two pytest runs sharing a pair delete each
other's rows and kill each other's connections — which surfaces as
`asyncpg.ConnectionDoesNotExistError` / `ConnectionResetError` in whatever
unrelated file happened to be mid-query, i.e. as flakiness rather than as the
collision it is.

Exclusivity is therefore claimed, not assumed. On first use each process takes a
**slot** — a Postgres session-level advisory lock — and uses the tenant pair
(and, see below, the control-plane database) named for it:

| Slot | Tenants | Control-plane DB |
|------|---------|-------------------|
| 0 (first / only process) | `feoh_pytesta`, `feoh_pytestb` | `feohledger_pytest` |
| 1, 2, … (each further concurrent process) | `feoh_pytesta1`, `feoh_pytestb1`, … | `feohledger_pytest1`, … |

**The harness's `Organization`/`User`/`Role` rows live in their OWN per-slot
control-plane database, never the real, shared `feohledger`** (`settings.database_url`)
— unlike the tenant pair, this is true for slot 0 too. `control_db_name_for_slot`
in `tests/conftest.py` derives the name from the configured control-plane DB
(`<base>_pytest<slot>`), and every control-plane operation the harness performs
— seeding roles/orgs/users in `_ensure_test_tenants`, `RealDB.control_sessionmaker()`,
the `get_control_db` override in `RealDB.client()` — targets it instead. This is
what closes a real defect (see `docs/known-issues.md`): a locally-running dev
backend's background sweeps (e.g. `extraction_reaper.run_reaper_loop`) enumerate
**every** `Organization` row in whatever database `settings.database_url` names,
with no filter. When the harness's test orgs lived there too, a dev server
sweeping the real control plane would discover and mutate them mid-test — stray
`TRUNCATE` stalls and invoices flipped to `failed` out from under a running
assertion. Giving every slot (0 included) its own control-plane database makes
the harness's tenants invisible to any server pointed at the default one. It
also removes the last cross-process contention: two concurrent slots no longer
share the control plane's unique constraints (org slugs, user emails) either.

Consequences worth knowing:

- **The common path pays a small, one-time-per-process cost, not a per-test
  one.** A lone `pytest` run — and every CI shard, each with its own fresh
  Postgres — still takes slot 0 and reuses the historical tenant names, but
  (like the tenant pair) it also provisions/self-heals its own
  `feohledger_pytest` the first time any test in the process requests `realdb`.
  Measured: roughly 2-3 extra seconds on that first call, not per test —
  negligible against a multi-thousand-test suite's total wall-clock.
- **A second concurrent run just works.** It provisions its own tenant pair
  *and* its own control-plane database on first use (one-off, per session) and
  cannot disturb the first. Run several agents or terminals at once without
  coordinating.
- **Crash-safe, nothing to clean up.** Postgres releases the lock when the
  holding connection dies, so a killed run frees its slot; the databases
  (tenant pair and control-plane DB alike) are reused by the next process to
  claim it.
- **Schema drift self-heals at session start** (issue #219) — for the tenant
  pair AND, the same way, for the per-slot control-plane database. Because
  they're long-lived and provisioned via `create_all(checkfirst=True)` — which
  never adds a column to an existing table — a database predating a later
  model change used to stay (or, absent this fix, would stay) silently stale
  until something read the missing column. The harness drops and recreates
  each pre-existing database's schema from the current `Base.metadata` once per
  pytest session (on the first `_ensure_test_tenants` call, keyed like the slot
  claim, via the shared `_rebuild_pytest_schema` helper), so a local slot can
  never lag the ORM. Fresh provisioning (CI shards, a new slot) skips the
  rebuild — it's already current by construction. Guarded by
  `test_realdb_harness.py`.
- **Never hardcode a test tenant's slug or a seeded login** — they carry the slot
  number. Use `realdb.info("a").slug` / `realdb.email("a", "admin")`.
- A control-plane row a test creates still needs a unique value per SESSION
  within its own slot's database (derive it from the slug or a uuid) — that
  database is long-lived across separate pytest invocations of the same slot,
  even though it's no longer shared with any other slot.

The harness resets tenant tables plus `Organization.settings` / `parent_org_id`.
It does **not** delete extra control-plane `users` rows a test creates — those
accumulate, so a test must not assume a fixed user count for a test org.

**The literal default `settings.database_url` still gets its own baseline**
(`_ensure_default_control_schema`, once per process) — CONTROL_TABLES schema
plus the four seeded system roles, but never any Organization/User/UserRole
row. CI's backend-test job never runs Alembic migrations against `feohledger`
(the Postgres service just creates the empty database); before the per-slot
control DB existed, slot 0 *was* that literal connection string, so its
ordinary bootstrap incidentally satisfied this. `test_tenant_provisioning.py`
deliberately exercises the real `settings.database_url` production path
end-to-end (self-contained — it creates and cleans up its own org/user rows),
and this harness's own isolation regression test needs the `organizations`
table to exist so "no matching row" actually proves isolation instead of
erroring on a missing table. Locally this is a no-op against a real, migrated
`feohledger` (`create_all(checkfirst=True)` + `ON CONFLICT DO NOTHING`); it
only does real work against a genuinely fresh Postgres (CI, or a from-scratch
local instance).

`tests/test_realdb_harness.py` guards both properties.

## Date-sensitive tests — run them under a skewed timezone

The backend resolves "today" as `app/utils/dates.utc_today()`, never the
server's local date, and `tests/test_utc_today.py` AST-scans the converged
modules to keep it that way. **A test is not exempt from that rule.** When a
test anchors its fixtures on `date.today()` (local) and the code under test
compares against the UTC date, the two disagree for the whole window each day
where the local calendar date differs from UTC's — several hours daily anywhere
west of UTC, and the entire working day in Asia-Pacific.

CI runners are UTC, so the suite reads green there no matter how wrong the test
is. The failure surfaces only on a contributor's laptop, as an
unreproducible-looking date assertion. Don't wait for a clock — force it:

```bash
# UTC+14, so the LOCAL date is a day AHEAD of UTC. Deterministic, any hour.
TZ=Pacific/Kiritimati pytest -q
```

Run that whenever you add or touch a test that compares a date against
something the app computed. It turns the entire class of bug from an
intermittent flake into a hard failure. Five test modules were wrong this way
(past-due + future-date fraud flags, the W-9 received-date stamp on both the
`/tax` and `/portal` surfaces, aging-band boundaries, and the upcoming-payment
overdue inequality); `UTC_TODAY_TEST_MODULES` in `tests/test_utc_today.py` is
the allowlist that stops them regressing.

Reading the local date in a test is not automatically a bug: 31 other test
modules do it self-consistently — fixture and assertion from the same sample —
and are deliberately not on that allowlist. What matters is whether the value
gets compared against one the app derived.

## Project structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app, CORS, router includes, lifespan
│   ├── config.py            # Pydantic Settings (FEOH_ prefix env vars)
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

1. **Control plane** (`feohledger`) — shared across all tenants
   - `Organization` — id, name, slug, db_name, settings (JSONB), plan
   - `User` — email, full_name, hashed_password, sso_provider/id, mfa_secret/enabled/enrolled_at, must_change_password, notification_prefs (JSONB — per-user email/in-app channel prefs, user-global), device_tokens (JSONB — one mobile push token per platform, registration only, no push-sending adapter yet; migration 0078), organization_id
   - `Role` — name (admin, ap_manager, ap_clerk, cfo)
   - `UserRole` — junction table
   - `WebAuthnCredential` — registered passkey (credential_id, public_key, sign_count, transports) per `user_id`; the WebAuthn second factor (migration 0063)
   - `AssistantUsage` — billing: per-org/month assistant token meter. The
     *only* usage meter that is control-plane; `ExtractionUsage` and
     `CardRebate` read like control-plane data but are tenant-local (see
     the tenant list below and `docs/decisions.md` §57)

2. **Tenant DBs** (`feoh_<slug>`) — isolated per customer
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
   - `Payment` — invoice_id, payment_run_id, amount, method (`String(50)`, not a DB enum — `ach`/`wire`/`check`/`virtual_card`, the international rails `sepa`/`international_ach`/`international_wire`, and the UK domestic rails `bacs`/`faster_payments`/`chaps`; classified on both the 1099 and geography axes in `services/payment_methods.py`), status, `retry_of_payment_id` (self-FK, migration 0080 — `/runs/{id}/retry-failed` books a NEW attempt row pointing at the failed one it replaces and never mutates that row, because `correlation_id` is the PROCESSOR's idempotency key; run rollups count the latest attempt per invoice via `payment_runs.active_run_payments`. See `docs/payments.md` § Why a payment failed, and retrying it), `settled_amount` / `settled_currency` (migration 0083 — what the PROCESSOR says it moved, beside `amount` which is what AP AUTHORIZED. NULL is meaningful and is not zero: no rail ever reported a figure, which `payment_settlement.settlement_coverage` reads as "nothing indicates a shortfall" and fails OPEN, so an amount-free rail can't hold every invoice it settles. See `docs/payments.md` § Settlement-amount verification)
   - `VirtualCard` — invoice_id, card_provider (lithic/nium), provider_card_id, amount_limit, status
   - `CardRebate` — virtual_card_id, amount, rate, status (`pending`/`confirmed`/`paid_out`), period. Not in `CONTROL_TABLES` — fanned to every tenant DB like the rest, despite living in `app/models/virtual_card.py` alongside `VirtualCard`
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

**Commit-before-response (durability).** Both providers call
`database.commit_before_response(session, request)`, which registers the
success-path commit on the exit stack FastAPI unwinds *before*
`await response(scope, receive, send)`. Their post-`yield` `commit()` is now a
conditional backstop (`if session.in_transaction()`), not the primary path —
FastAPI runs that teardown **after** the client already has its `201`, so
relying on it acknowledged writes that weren't durable yet. Consequences:

- **Don't "simplify" the providers back to a bare post-`yield` commit** — that
  silently reinstates the bug. `tests/test_commit_before_response.py` pins the
  ordering and drift-guards the FastAPI internal (`fastapi_function_astack`).
- **A test that overrides `get_control_db` / `get_tenant_db` must mirror this.**
  The `realdb.client()` overrides do; they exist to swap the *engine*, not the
  commit semantics. An override with the old body makes every test in that file
  exercise a code path production no longer uses.
- **Any new session provider needs the same call.** Rationale + the rejected
  alternatives: `../docs/decisions.md` §20.

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

**Notification hook**: `transition_invoice()` is also the single chokepoint for invoice-event notifications — after the audit write it calls `notification_dispatch.notify_event()` keyed off the resulting status (`approved`/`rejected`/`paid`). The `invoice_assigned` event is fired separately from `review.assign_reviewer`. All best-effort (never breaks the transition). **The outbound legs — every email and the chat post — run AFTER the caller's transaction commits**, via `services/post_commit.enqueue_post_commit` and SQLAlchemy's `after_commit`; only the in-app `Notification` rows ride the caller's commit, because those are DB writes. Before this the fan-out was awaited inside the still-open transaction, so a hung chat webhook held `payment_erp_sync`'s / `review.approve_invoice`'s `FOR UPDATE` row lock on a live invoice for its full 10-second timeout, and N recipients multiplied the email leg linearly. A transaction that rolls back now sends nothing. See `docs/notifications.md` § The outbound legs run POST-COMMIT.

## Key background services

| Service | What it does |
|---------|-------------|
| `services/extraction_reaper.py` | Sweeps every tenant DB on a timer; transitions invoices stuck in `pending` extraction to `failed`. |
| `services/audit_log_shipper.py` | Centralized audit-log shipper (SOC 2). Sweeps every tenant DB, reads unshipped `audit_log` rows in batches, fans them out to every configured `audit_shipping` adapter (CloudWatch Logs + S3 Object Lock), then marks `shipped_at=now()`. All adapters must ACK before rows are marked; failures leave rows unshipped so the next tick retries. Disabled by default — flip `FEOH_AUDIT_SHIPPING_ENABLED` on in deployed envs. See `docs/audit-log-shipping.md`. |
| `services/approval_escalation.py` | Sweeps every tenant's active workflow instances and appends `escalation_to_user_ids` onto any approval chain level waiting longer than its configured `escalation_hours`. Disabled by default (`FEOH_APPROVAL_ESCALATION_ENABLED`); flip on in deployed envs. |
| `services/payment_reconciler.py` | Backstop polling for payments whose processor webhook went missing. Re-fetches status from the payment adapter when a `submitted`/`processing` payment sits longer than `FEOH_PAYMENT_RECONCILE_AFTER_MINUTES`. Disabled by default (`FEOH_PAYMENT_RECONCILE_ENABLED`); flip on in deployed envs alongside Modern Treasury. |
| `services/contract_renewal.py` | Contract renewal-alert sweep. Sweeps every tenant DB; finds `active` contracts within their own `renewal_notice_days` of `end_date` with no alert sent, notifies the owner + AP managers once (`contract_renewal_due` event), then stamps `renewal_alert_sent_at` for idempotency (cleared on `POST /api/contracts/{id}/renew`). Same tick also transitions any `active` contract whose `end_date` has actually passed to `expired` (`contract.expired` audit row) — the only runtime path that sets `ContractStatus.expired`; idempotent (status guard). Disabled by default (`FEOH_CONTRACT_RENEWAL_ENABLED`); `FEOH_CONTRACT_RENEWAL_INTERVAL_SECONDS` / `_DEFAULT_NOTICE_DAYS`. See `docs/contracts.md`. |
| `services/discount_auto_trigger.py` | Dynamic-discounting auto-capture sweep. Sweeps every tenant DB; auto-accepts `offered` `DiscountOffer`s whose annualized ROI clears `FEOH_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD`, writing a `discount_offer.auto_accepted` audit row. **Only flags `offered → accepted` — never creates a Payment/PaymentRun**; the status guard is the dedupe. Disabled by default (`FEOH_DISCOUNT_OPTIMIZATION_ENABLED`). See `docs/dynamic-discounting.md`. |
| `services/retention_sweep.py` | Retention-policy enforcement sweep (SOX records management). Sweeps every tenant DB; soft-archives overdue terminal (`done`/`paid`) invoices via a `meta.archived_at` marker (idempotent — re-run never double-archives) and writes a `retention.archived` manifest. **Composes with the audit-immutability trigger — NEVER deletes `audit_log` rows**; for the audit class "retention" verifies WORM shipment (`shipped_at`) + records overdue/unshipped counts only. Windows are per-class on `Organization.settings.retention` (`resolve_retention_months`); `GET/PUT /api/retention-policy` reads/updates them. Disabled by default (`FEOH_RETENTION_ENABLED`); `FEOH_RETENTION_INTERVAL_SECONDS` / `_DEFAULT_MONTHS`. See `docs/retention.md`. |
| `services/recurring_invoices.py` | Recurring / subscription invoice generation sweep. Sweeps every tenant DB; finds `active` `RecurringInvoiceTemplate`s whose `next_run_on` has arrived, generates the next pre-coded `Invoice` into the approval queue (period_key `YYYY-MM` / `YYYY-Qn` / `YYYY`), advances `next_run_on`, and writes a `recurring_template.generated` audit row. **Idempotent on `(template, period_key)`** via the partial unique index `uq_invoice_recurring_period` (a double-fire never double-creates); **only creates an Invoice in the queue — never creates a Payment/PaymentRun**, exactly like `discount_auto_trigger`. Per-tenant cap `FEOH_RECURRING_INVOICES_MAX_PER_SWEEP`. Disabled by default (`FEOH_RECURRING_INVOICES_ENABLED`); `FEOH_RECURRING_INVOICES_INTERVAL_SECONDS`. See `docs/recurring-invoices.md`. |
| `services/cash_flow_alerts.py` | Projected-cash-shortfall alert sweep. Sweeps every org; skips any without a persisted `settings.cashflow.min_balance_threshold` (the threshold IS the opt-in), builds the org-wide commitment rows (`entity_id=None` — a treasury shortfall is a whole-group question, the same posture `GET /analytics/by-entity` takes), resolves the opening balance through the shared `services/cashflow.resolve_opening_balance`, and runs the pure `compute_cash_position` → `detect_threshold_breaches`. Notifies the finance leaders (`ALERT_ROLES`, drift-guarded against `api/cash_flow.py::COPILOT_ROLES`) once per projected shortfall period via `cash_shortfall_projected` (`entity_type="cash_position"`, `entity_id` NULL). **Never moves money** — no Payment/PaymentRun, no discount, no invoice; the only write beyond the notification rows is the alerted-period marker on the org's settings JSON, which is also the dedupe (cleared when the projection clears, so a recurrence re-alerts). Notify happens BEFORE the marker write so a crash between them re-alerts rather than swallowing the warning. Disabled by default (`FEOH_CASHFLOW_SHORTFALL_ALERTS_ENABLED`); `FEOH_CASHFLOW_SHORTFALL_ALERTS_INTERVAL_SECONDS` / `_HORIZON_DAYS`. See `../docs/cash-flow-copilot.md` § Proactive projected-shortfall alerts. |
| `services/scheduled_reports.py` | Scheduled-report runner. `run_scheduled_reports_once` sweeps every tenant DB; runs each `enabled` schedule whose `next_run_at` has arrived (`execute_schedule`: generate CSV via `report_export` → email recipients → advance `next_run_at` / persist a `[retry N]` failure marker, auto-disabling after 5 consecutive failures). `next_run_at` advances via `advance_next_run` from the slot the run was **due** at, never from the tick that picked it up — anchoring on the wall clock made a "daily 09:00" report land later every day and walk around the clock inside a month; a missed window catches up in whole cadence steps to the first future slot, never a backlog burst. "Today" for each report's `period_days` window is `utils/dates.utc_today()`, matching the `/analytics` exports and the copilot. One tenant's failure never halts the sweep. Disabled by default (`FEOH_SCHEDULED_REPORTS_ENABLED`); `FEOH_SCHEDULED_REPORTS_TICK_SECONDS`. Its input surface is `api/scheduled_reports.py` (`/api/analytics/scheduled-reports`, admin-only to mutate). See `docs/analytics.md` § Scheduled report delivery. |

| `services/sweep_health.py` | **The mechanism every sweep above shares.** `run_sweep_loop` is the single loop body each `run_*_loop` delegates to — it ticks, records the outcome into an in-process registry (last-run timestamps, `ok`/`partial`/`error`, the consecutive-failure streak, the integer counters the sweep's own result dataclass reported), sleeps, and re-raises `CancelledError` on shutdown. **A tick that completes reporting `failures > 0` is a failed run, not a healthy one** — the case that used to make a months-long broken `audit_shipping` sink indistinguishable from a clean platform. Past `FEOH_SWEEP_FAILURE_ALERT_STREAK` (default 3) the sweep is `degraded` and the loop emits the alertable PII-free `NOT MAKING PROGRESS` ERROR, on streak multiples so a 60-second sweep can't drown the sink. `supervise_task` attaches the `add_done_callback` that records a sweep task **dying** (vs. being cancelled at shutdown) — every start in `main.lifespan` goes through the `start_sweep()` helper, drift-guarded by an AST scan. Only exception **class names** are ever recorded or logged, never `exc_info` (the stdlib appends the traceback text regardless of the format string). Read at `GET /api/health/sweeps`. See `docs/background-sweeps.md`. |

These long-lived asyncio tasks are started in `main.lifespan` (each behind its `FEOH_*_ENABLED` gate) via `start_sweep()` — supervised — and cancelled on shutdown. Their per-tick health is queryable at **`GET /api/health/sweeps`** (admin-only; process-local; no cross-tenant cardinality in the payload). `GET /api/health` stays the public static liveness probe and deliberately does NOT fold sweep health in — a degraded sweep is no reason to pull a healthy process out of rotation. See `docs/background-sweeps.md` and `../docs/decisions.md` §24.

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

**Platform-mode provider precedence** is the pure `extraction.resolve_platform_provider`
(BYOK never reaches it): `FEOH_EXTRACTION_PROVIDER` → a set `FEOH_ANTHROPIC_API_KEY`
→ `claude_vision` (**the deployed path, unchanged**) → keyless + non-deployed →
`mock`. The last rung is what makes extraction local-first — platform mode used to
be hardcoded to `claude_vision` *whether or not a key existed*, so a fresh clone
POSTed to `api.anthropic.com` with an empty key and every extraction (invoice and
PDF supplier statement alike) came back `provider_error`. A keyless **deployed**
env deliberately does NOT fall back: `mock.extract` returns a fixture, and
fabricating invoice fields on a real tenant's document is worse than the loud
provider error. Both fallback rungs log a PII-free WARNING and stamp
`platform_provider_reason` on the config; the chosen provider rides the persisted
result (`InvoiceExtractionResult.method` / a statement run's
`meta.extraction.provider`). An unregistered `FEOH_EXTRACTION_PROVIDER` is refused
at boot (`config.py::_validate_extraction_provider` + its registry drift guard).
See `docs/ai-extraction.md` § Platform provider precedence and `../docs/decisions.md` §26.

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

Config `integration_method: "merge_dev"|"direct"` selects whether to use Merge.dev unified API or direct adapter. Note `integration_method` **defaults to `merge_dev`**, so a config naming only a `type` routes through Merge.dev regardless of that type.

An ERP type with no registered adapter raises `UnknownErpAdapterError` — it used to fall back to `mock`, whose `post_invoice` returns `success=True` with a fabricated `MOCK-…` document id, so `services/erp` walked the invoice `sending_to_erp → sent_to_erp → done` carrying an ERP reference that pointed at nothing, and `POST /api/organization/test-erp` answered "Connected successfully" (`mock.test_connection()` is `True`). The three sync endpoints now 400, and test-erp names the bad value. In `payment_erp_sync` the adapter is resolved **inside `_sync_one_leg`**, where it would be used, so an unsupported type fails that leg and opens the de-duped `erp_reconciliation` exception like any other leg failure — a pre-flight check before the tenant session could not open one, and its count is discarded on the fire-and-forget dispatch path, which would strand the run invisibly. See `../docs/decisions.md` §29.

ERP send has retry logic: up to 3 attempts with exponential backoff (2s, 4s, 8s).

The three real adapters' provider base URLs are env-overridable via the
operator-trusted `FEOH_ERP_MERGE_API_BASE` / `FEOH_ERP_NETSUITE_API_BASE` /
`FEOH_ERP_D365_API_BASE` / `FEOH_ERP_D365_TOKEN_URL` (process-level, so they bypass
the admin-config SSRF guard; an admin-supplied `base_url` stays guarded).
`backend/.env.development` points all four at the local fake ERP server — the
`fake-erp` compose service (opt-in `erp` profile, :12112, built from
`tools/fake-erp/`, deterministic PO/GL fixtures, shape-checked auth only) — so
`pnpm erp:up` → `pnpm test:erp` exercises `merge_dev`/`netsuite`/
`dynamics_365_bc` end-to-end with no cloud account. See
`docs/erp-integration.md` § Local e2e testing (fake ERP server).

### Card adapters (`services/card_adapters/`)

Registered: `lithic`, `nium`, `mock`. Both real providers have sandbox modes.

**A named unsupported provider fails closed.** `get_card_adapter` resolves a MISSING `settings.cards.provider` through `REGION_DEFAULTS` (local-first) but raises `UnknownCardProviderError` for a NAMED provider it has no adapter for. It used to fall back to `mock`, which is not an inert stub — `create_card` returns `success=True` with a `mock_card_...` id and `last_four="4242"`, `get_card_details` returns the fixture PAN `4242424242424242`, `cancel_card` returns `True` unconditionally — so one typo in an admin-entered provider name made every issuance "succeed": rows landed with `card_provider="mock"`, the payment-run card leg marked each payment `completed` and each invoice `payment_scheduled`, and vendors were emailed reveal links resolving to a fixture PAN. This is the one dispatcher family `../docs/decisions.md` §29 missed (§36 did the same for sanctions). Each caller decides what the refusal means: `issue_card_for_invoice` returns `failure_reason="card_provider_not_configured"` (no provider call, so RETRY_SAFE), `POST /api/cards/generate` 409s the batch, `/details` and `/cancel` 409 (the row stays `active`), `cancel_card_at_provider` records `card_provider_not_configured` rather than a cancel it never obtained, and the vendor PAN reveal degrades to its PII-free body. Guard: `tests/test_card_provider_resolution.py`.

Card creation is **idempotent at the provider**, not only in our DB. The partial
unique index `uq_virtual_cards_one_live_per_invoice` only catches duplicates
that reached our database — an `httpx` timeout *after* the provider provisioned
the card writes no row, so an unkeyed retry mints a second live card while the
first is orphaned. `services/card_issuance.build_card_idempotency_key` mints a
pure, deterministic UUID5 (`correlation_id or invoice_id` + a re-issue sequence
read from the invoice's existing card rows — never a fresh `uuid4`), carried on
`VirtualCardPayload.idempotency_key` and sent by each adapter on its provider's
own channel: **Lithic** `Idempotency-Key` header (must be a UUID, 30-day
retention), **Nium** `x-request-id` header (24-hour retention), **mock** derives
the card id from it so the retry path is exercisable locally. The re-issue
sequence is what keeps a deliberate cancel-then-reissue from replaying the
original closed card. `issue_card_for_invoice` therefore takes `db`. See
`docs/virtual-cards.md` § Issue.

### Payment adapters (`services/payment_adapters/`)

```python
@register_payment_adapter("my_processor")
class MyAdapter(PaymentAdapter):
    async def create_payment(self, payload: PaymentPayload) -> PaymentResult: ...
    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus: ...
    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None: ...
    async def test_connection(self) -> bool: ...
    # OPTIONAL — all four fail closed on the base (available=False):
    async def quote_payment(self, payload: PaymentPayload) -> CorridorQuote: ...
    async def get_balance(self) -> BalanceResult: ...
    async def fetch_settlement(self, provider_payment_id: str) -> SettlementReport: ...
    async def void_payment(self, provider_payment_id: str) -> bool: ...
```

Registered: `mock`, `modern_treasury`, `stripe_treasury`, `increase`, `column`, `dwolla` (ACH only), `checkeeper` (check printing).

**An unsupported provider name fails closed.** `get_payment_adapter` resolves an
absent/empty `settings.payments.provider` to `mock` (the local-first default) but
raises `UnknownPaymentProviderError` for a NAMED provider it has no adapter for.
It used to fall back to `mock` there too, and `mock` is not an inert stub — its
`create_payment` returns `success=True, status=completed` immediately, its
`parse_webhook` verifies no signature, its `void_payment` returns `True`
unconditionally — so one typo in an admin-entered settings value made every
payment report as settled with no money moved, and served the public webhook
route to an unverified parser under a name the `provider == "mock"` early-return
cannot catch. `erp_adapters` and `fx_adapters` had the identical fallback and now
raise `UnknownErpAdapterError` / `UnknownFxProviderError`; the FX one is the
sharpest, because `prepare_international_payment` LOCKS the rate it gets onto the
Payment row and never re-fetches it. Each caller decides what the refusal means
(refuse before claiming a run; fail the one payment; degrade; count a sweep
failure) — the table is in `docs/payments.md` § Provider resolution, the
rationale in `../docs/decisions.md` §29. Guard:
`tests/test_payment_provider_resolution.py`.

**The four optional capabilities are drift-guarded** by
`tests/test_payment_adapter_capabilities.py` (same shape as
`test_payment_methods.py`, which guards the *rails* an adapter offers): every
registered adapter must either implement each capability or be listed there as
deliberately not implementing it, **with the consequence for the caller written
down**. Registering a processor that silently inherits all four is otherwise
invisible — the corridor auction skips it, the cash-position curve falls back to
the manual opening balance, its settlements stay `unverified`, and `/void` books
a bookkeeping-only void while the money is still in flight — because in every
case the inherited code "works".

`quote_payment`'s base default is the one that had to *change* to fail closed.
It returned a fabricated `available=True` zero-fee, zero-ETA quote for any
supported method, and `corridor_quotes._rank` orders on realised cost then ETA —
so an adapter inheriting it beat every sibling publishing a real fee on BOTH
`cheapest` and `fastest`, unconditionally, and `savings_vs_runner_up` reported an
invented saving against it. `modern_treasury` is that adapter. It now reports
`no_quote_endpoint` and is skipped until its real fee table lands. (`compare_quotes`
currently has no production caller — this was a latent trap, not a live
mis-route.) See `docs/international-payments.md` § Multi-route quote optimization.

`fetch_settlement` is the **pull** counterpart to the settled amount a webhook
pushes on `WebhookEvent`. Two paths knew a payment completed but never its
amount — Dwolla (a bare `{id, topic, resourceId}` envelope; the figure needs an
async re-fetch the synchronous signature path must not make) and the reconciler
backstop (`get_payment_status` returns a bare status by design) — so both
settled `unverified`. Implemented for `dwolla` + `mock`; called by the webhook
handler only when the event carried no amount, and by the reconciler whenever
it settles a payment. Both call sites are guarded: any failure leaves the
verdict `unverified`, never breaking the webhook or halting the sweep.

Minor-unit amounts go through `base.to_minor_units` / `minor_units_to_decimal`,
which are exact inverses and resolve the currency's **real ISO-4217 exponent**
(0 for JPY/KRW, 3 for BHD/KWD/OMR, 2 otherwise). Both legs must always move
together — they were a symmetric flat `* 100` pair, and fixing only the parse
side would turn a symmetric error into a live 100x mispricing.

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

Registered: `csv` (default), `fixed_width`. `get_positive_pay_formatter(name)` resolves a MISSING name to `csv` (the local-first default) and raises `UnknownPositivePayFormatError` for a NAMED layout it has no formatter for — both generate routes turn that into a 422 naming the bad value. It used to fall back to `csv` there too, which stored a CSV body under the requested format name, stamped the row + audit trail with it and burned the `(run, bank_format)` idempotency slot — so a typo left the tenant believing this fraud control was in force on a file its bank cannot parse (`../docs/decisions.md` §29 / §36 applied to this family). `fixed_width` additionally raises `PositivePayFieldOverflow` when an identifier or an amount cannot occupy its column without becoming a *different value* — both generate routes turn that into a 422 naming the column and never the value (these are full account numbers), and no file, row or audit entry is written, so the `(run, bank_format)` idempotency slot stays free for a corrected retry. Descriptive text (payee, vendor name, status) still truncates, which is the intended layout behaviour. The pre-fix renderer padded then sliced, keeping HIGH-order digits, so an overrunning amount was rescaled by ten per dropped digit; the drawee account column was also 8 chars against a FULL account number, making the check-issue record 89 chars once widened to 17. Renders a Positive Pay fraud-control file from the formatter dataclasses (`CheckIssueItem` / `AchAuthorizationItem` / `FormatterContext` in `base.py`); the async DB→dataclass builders + the pure return classifier (`matched_ok` / `amount_mismatch` / `not_on_file`) live in `services/positive_pay.py`. The rendered file legitimately holds full account/routing numbers and is stored in MinIO via `storage.upload_positive_pay_file`; the `PositivePayFile` DB row + audit/logs/errors are PII-free (`account_last4` only). Mounted at `/api/positive-pay`. Idempotent per `(payment_run_id, bank_format)` via the partial unique index `uq_positive_pay_run_format`. See `docs/positive-pay.md`.

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

An FX provider with no registered adapter raises `UnknownFxProviderError` (absent/empty still resolves to `mock` — the local-first default). This is the sharpest of the three fail-closed dispatchers: the rate is locked onto the Payment row once and never re-fetched, so the old `mock` fallback wrote a plausible-but-wrong figure off a hardcoded table that then drove the real outflow and `realized_fx_gain_loss_for_settlement`. The international leg now fails the payment with `failure_reason="fx_provider_unsupported"`; expenses refuse the attach / leave the report figure NULL so the CFO gate fails closed; the CFO dashboard reports `available: false`. See `../docs/decisions.md` §29.

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

Registered: `mock` (deterministic synthetic firmographics, no network/credential — the local-first default), `dun_bradstreet` + `clearbit` (httpx skeletons — live key via per-org settings; **fail closed** `EnrichmentNotConfigured` without it, no hardcoded fallback). `get_enrichment_adapter(config)` resolves `Organization.settings.enrichment.provider` → `FEOH_VENDOR_ENRICHMENT_PROVIDER` → `mock` (an absent/empty provider is the local-first default), and raises `UnknownEnrichmentProviderError` for a NAMED provider it has no adapter for — the enrich route turns that into a 422. It used to fall back to `mock`, which fabricates a complete plausible identity (legal name / address / DUNS / employee count) with `matched=True`, so a typo presented invented firmographics as a D&B lookup one click from being applied onto a real supplier (`../docs/decisions.md` §29 / §36). External vendor firmographics (legal name / registered address / industry+SIC/NAICS / employee count / revenue / website / DUNS / founding year) for `POST /api/enrichment/vendors/{id}/enrich`. **Advisory / suggestion-only** — returns the firmographics + a per-field suggestion diff but NEVER writes back onto the `Vendor` row. Raw `tax_id` is an input match-key only — never echoed (only `***<last4>` via `mask_tax_id`), never logged. See `docs/data-enrichment.md` § External enrichment.

### Audit-shipping adapters (`services/audit_shipping/`)

```python
@register_audit_shipping_adapter("my_sink")
class MySinkAdapter(AuditShippingAdapter):
    async def ship(self, rows: list[AuditLogRow]) -> None: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock`, `cloudwatch`, `s3_objectlock`.

The `audit_log_shipper` background loop instantiates every adapter named in `FEOH_AUDIT_SHIPPING_PROVIDERS` and ships each batch to all of them; all must succeed before the rows are marked shipped. See `docs/audit-log-shipping.md`.

### TIN-validation adapters (`services/tin_validation_adapters/`)

```python
@register_tin_validation_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"
    def __init__(self, config: dict | None = None): ...
    async def validate(self, *, tin, legal_name=None, tin_type_hint=None) -> TINValidationResult: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock` (offline EIN/SSN format + IRS structural rules — the local-first default), `tax1099` (IRS TIN-match skeleton — live key required; degrades to format-only without a key). Selected per-org via `Organization.settings.tax.tin_validation` → falls back to `FEOH_TIN_VALIDATION_PROVIDER` (default `mock`). Results carry only the verdict + redacted last-4 — never the raw TIN. Wired at `POST /api/tax/vendors/{id}/tin-verify`. See `docs/tax-1099.md`.

### 1099 e-filing adapters (`services/tax_filing_adapters/`)

```python
@register_tax_filing_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"
    def __init__(self, config: dict | None = None): ...
    async def submit_batch(self, *, tax_year, forms, idempotency_key) -> FilingBatchResult: ...
    async def test_connection(self) -> bool: ...
```

Registered: `mock` (offline, deterministic, idempotent — the local-first default), `tax1099` (partner e-file skeleton — live key required). Selected per-org via `Organization.settings.tax.filing` → falls back to `FEOH_TAX_FILING_PROVIDER` (default `mock`). `POST /api/tax/1099/file` is idempotent on `(organization_id, idempotency_key)` via the `tax_1099_filings` table (a duplicate IRS filing is a real problem); the filing row carries no recipient TIN. See `docs/tax-1099.md`.

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

Registered: `mock` (in-process, no network — the **local-first default**), `as4_gateway` (real — `httpx` to a hosted Access Point; key via sops, no hardcoded fallback). Selection via `Organization.settings.peppol.provider` → `FEOH_PEPPOL_PROVIDER` (default `mock`). Outbound **send** turns an invoice into UBL via the `e_invoice` package, resolves the receiver via SMP/SML (`resolve_participant`), and transmits via the gateway; SBDH wrapping lives in the adapter, never the generator. `services/peppol_send.send_invoice_over_peppol` orchestrates it (map → tax-validate → UBL → resolve → INSERT `peppol_transmissions('sending')` → send → audit), idempotent at the DB layer. Route `POST /api/invoices/{id}/peppol-send`.

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
`Organization.settings.punchout.provider` → `FEOH_PUNCHOUT_PROVIDER` (default
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
| `FEOH_PUNCHOUT_PROVIDER` | `mock` | Adapter — `mock` \| `cxml`. Per-org override `Organization.settings.punchout.provider`. |
| `FEOH_PUNCHOUT_SHARED_SECRET` | (empty) | cXML supplier credential — no hardcoded fallback; sops in deployed. |
| `FEOH_PUNCHOUT_RETURN_SIGNING_SECRET` | (empty) | HMAC key the supplier signs the cart-return POST with. No hardcoded fallback; committed `.env.development` sets a NON-secret dev value. |
| `FEOH_PUNCHOUT_RETURN_MAX_BYTES` | `4194304` | Cart-return body cap (memory-exhaustion guard). |

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
Selection via `Organization.settings.billing.provider` → `FEOH_BILLING_PROVIDER`
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
audited), the `GET /api/billing/plans` catalog endpoint (admin/cfo, active
plans only, cheapest first — the plan-change picker's data source), and the
invoices/receipts list endpoint (`GET /api/billing/invoices`,
admin/cfo, money as exact strings, graceful empty-list on no-customer /
unconfigured), and the payment-method endpoint (`POST
/api/billing/payment-method/setup-intent` + `GET /api/billing/payment-methods`,
admin/cfo, PII-safe card metadata only, graceful not-configured / empty on
no-customer / unconfigured) are shipped; the invoices/receipts + payment-method
UI ships on `/billing` (`frontend/src/routes/billing/` — saved-cards list +
add/replace-card SetupIntent flow with a deployed-only Stripe Elements seam),
and so does the **live plan-change UI** — a `Modal` picker over `GET
/api/billing/plans` → an "applies immediately, prorates the current period"
notice (there is no preview-only mode on the backend) → `POST
/api/billing/change-plan` on confirm → the result view renders the real
returned proration via `<Money>` (or a clean no-op message when `changed`
comes back `false`). See `docs/billing.md`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `FEOH_BILLING_PROVIDER` | `mock` | Billing adapter — `mock` \| `stripe_billing`. Per-org override `Organization.settings.billing.provider`. |
| `FEOH_BILLING_STRIPE_API_KEY` | (empty) | Live Stripe Billing key — no hardcoded fallback; sops in deployed. Adapter fails closed without it. |
| `FEOH_BILLING_STRIPE_WEBHOOK_SECRET` | (empty) | HMAC secret for Stripe webhook verification — no fallback; sops in deployed. |

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
| `/api/email-intake/inbound/{provider}` | `FEOH_EMAIL_INTAKE_SIGNING_SECRET` (process-level HMAC key; verified in `email_intake.verify_signature`). Dedupe is `is_event_already_processed("email_intake", message_id)`, claimed right after tenant resolution and released via `release_event_claim` if invoice creation fails downstream (mirrors `api/cards.py`'s claim/release discipline) so a redelivery can retry. Recipient-token match uses `hmac.compare_digest`. |
| `/api/peppol/inbound/{tenant_slug}` | `FEOH_PEPPOL_INBOUND_SIGNING_SECRET` (process-level HMAC key; verified by `peppol_receive.verify_inbound_signature`). Dedupe is the DB `uq_peppol_message_id` index, not Redis. |
| `/api/catalogs/punchout/return/{tenant_slug}` | `FEOH_PUNCHOUT_RETURN_SIGNING_SECRET` (process-level HMAC key; verified in `catalogs._verify_return_signature`). Correlation is the BuyerCookie matched to a pending `PunchoutSession`. |
| `/api/approvals/slack/interactivity` | `FEOH_SLACK_SIGNING_SECRET` (process-level HMAC key; verified in `slack_approvals._verify_slack_signature` over `v0:{X-Slack-Request-Timestamp}:{raw_body}`, with a `±FEOH_SLACK_REQUEST_MAX_AGE_SECONDS` replay window). Per-action dedupe is the single-use action-token `jti` in Redis (the email-approval mechanism), not `is_event_already_processed`. Returns an opaque 200 ack (Slack-friendly) on every path, not 204. See `docs/slack-approval.md`. |

Every webhook handler returns **204 silently** on every rejection path (bad signature, unknown tenant, missing event id, unknown card / invoice / payment, disabled master switch, unparseable / malformed inbound document) — except the Slack interactivity webhook, which returns an opaque **200 ack** on every path (Slack requires 2xx to acknowledge a button click; the ack text is identical across success and rejection, so it doesn't enumerate), and the email-intake webhook, which is a hybrid: pre-signature rejections (unknown provider / bad signature / unparseable body — nothing sensitive resolved yet) still return 204, but every *decision* **after** the signature verifies (unknown/disabled intake token, duplicate delivery, no usable attachments, or genuine success) returns the SAME opaque 200 ack (`email_intake._ack()`) — because unlike the other handlers, a 204-vs-200-with-a-body split there would itself be the token-enumeration oracle (the platform-wide signing secret is shared across all tenants, so anyone who can sign a request can watch for the response to change once they guess a valid intake token). Distinct 4xx responses / differing response bodies would enumerate tenant slugs or card/intake tokens.

**A *decision* is not the same as OUR OWN failure, and only the decision is acked.** A decision is a final answer about this message; a failure of ours (S3 unreachable, tenant DB down, Redis flapping, a concurrent transition racing the state-machine guard) is not an answer at all — the message is still unprocessed work. `email_intake` and `erp_webhook` both release their Redis dedup claim on those paths precisely so the provider's retry can reprocess, but both used to then ack success, so the retry never came: the release was preparing for something that could not happen, and the vendor's invoice / the ERP status update was dropped permanently behind a log line. Both now return a **bodyless 5xx** (`_retry_please()` in each module → `503`) on their own failures only, matching `api/billing_webhook.py`, which already re-raised to a 5xx for the same reason. This narrows the intake-token oracle to "while the platform is already broken" — a real but far smaller exposure than losing invoices on every blip — and the bodyless response still carries no detail, no stack trace, no tenant. Do NOT extend the 5xx to decision paths: an ERP retrying forever on an event we have correctly and permanently refused is the mirror-image failure.

Tests: `backend/tests/test_webhook_security.py`, `tests/test_payment_webhook_security.py`, `tests/test_peppol_inbound.py`, `tests/test_slack_approvals.py`, `tests/test_email_intake.py`, `tests/test_email_intake_processing.py`, `tests/test_erp_webhook_retry_on_our_failure.py`.

## Security utilities

- **Passwords**: a single shared `pwd_context` in `app/utils/passwords.py` uses `bcrypt_sha256` (SHA-256 pre-hash → bcrypt) to side-step bcrypt's 72-byte truncation. Every call site (auth, admin, portal, vendors, tenant_provisioning, scripts/seed) imports from there — never construct a fresh `CryptContext`. Complexity rules in `validate_password_complexity` (min 12 chars, upper/lower/digit).
- **Filename sanitiser**: `app/services/storage.py::_safe_filename` strips path separators, `..`, leading dots (no dotfiles), and control / non-printable characters. Used by `upload_invoice_file` before interpolating the filename into the S3 key. Without it, a vendor portal POST with filename `../../other-org/secret.pdf` could land under another tenant's prefix.
- **File download cross-tenant check**: `GET /api/workflow/file/{file_key:path}` verifies the key's first segment equals the requesting user's `organization_id`. Same 404 for wrong-org and missing-file so the response doesn't enumerate prefixes.
- **CSV formula-injection defense (CWE-1236)**: `app/services/report_export.py::csv_safe_cell` prefixes a leading `= + - @` / tab / CR (except a bare signed number) with a single quote so a spreadsheet renders the cell as text, not a formula. Attacker-controlled fields (AI-extracted `vendor_name`, `User.full_name`) reach CSVs a CFO/admin opens in Excel. Use `safe_csv_writer(buf)` (the sanitizing `csv.writer` stand-in) at **every** export writerow site; for `csv.DictWriter` sites sanitize the row values through `csv_safe_cell`. Covers the analytics/report exporters, audit export, ad-hoc report builder, invoice bulk export, and workflow export.

## Audit immutability + access auditing (SOX)

The `audit_log` table is **append-only at the database level**: migration `0022_sox_audit_immutable` installs `BEFORE` triggers (DDL in `app/services/audit_immutability.py`) that reject every DELETE and every UPDATE touching a column other than `shipped_at`. The `shipped_at` carve-out lets `audit_log_shipper.py` stamp shipped rows; everything else is frozen, so a rogue ORM call or a direct `psql` session can't tamper with the trail. Installed on every tenant DB — migration fan-out for existing tenants, `tenant_provisioning._create_tenant_tables` for fresh ones (which use `create_all`, not Alembic). See `docs/audit-log-shipping.md`.

Two request-path helpers in `app/services/audit_access.py` (thin wrappers over `dispatch_audit`, not reimplementations):
- `log_access(...)` — writes a `<entity_type>.viewed` row for SOX access-control auditing. Instrumented reads: vendor detail (`vendor.viewed`), payment detail (`payment.viewed`), card PAN reveal (`card.details_viewed`), the audit-trail view (`audit.viewed`), and every auditor export (`audit.exported`). The `details` payload records the field-**names** accessed, never the values — no tax id / bank number / PAN ever enters the audit trail (PII-out-of-logs).
- `build_field_diff(before, after, fields)` — produces `{field: {old, new}}` for SOX change history on invoice edits + approve-with-corrections. Money serialises as **string-Decimal**, never float.

The auditor-export surface is `app/api/audit.py` (`/api/audit/export`, `/api/audit/invoice/{id}` — GET-only, admin/CFO). `/api/audit/export` also serves a formatted **PDF** SOX audit-trail report via `?format=pdf` (cover + event-count summary + chronological table; `app/services/audit_report_pdf.py`, pure-function modelled on `remittance_pdf.py`; renders only the field-NAME-sanitised entries). See `docs/api-reference.md` § Audit Trail.

**Periodic access reviews (SOX)** — `app/api/access_reviews.py` (`GET /api/access-reviews` + `POST /api/access-reviews/acknowledge`, admin/CFO). Compute-on-read (no migration): `app/services/access_review.py` flags users holding an elevated role (`admin`/`ap_manager`/`cfo`) whose last *mutating* audit action is older than `FEOH_ACCESS_REVIEW_DORMANT_DAYS` (default 90), or who never acted, as DORMANT. The review list is itself a sensitive read (`access_review.viewed`); acknowledge writes `access_review.completed` + stamps `Organization.settings.access_review`. See `docs/access-reviews.md`.

**Digital signatures on approvals (non-repudiation):** every `invoice.approved` audit row carries an HMAC-SHA256 "timestamp + user hash" in `details.signature` over the canonical approval facts (invoice id + exact Decimal amount + actor + decision + timestamp). Signed in `services/review.approve_invoice` (`services/approval_signature.py` — pure); re-verifiable at `GET /api/audit/invoice/{id}/verify-signatures` (admin/CFO), where a post-approval tamper of the amount/actor/timestamp → `valid: false`. Key `FEOH_APPROVAL_SIGNING_KEY` — empty → signing skipped, NON-secret committed dev value, real key via sops (no hardcoded fallback). See `docs/approval-signatures.md`.

**Retention policies (records management):** per-record-class windows on `Organization.settings.retention` (`GET/PUT /api/retention-policy`, admin); the `retention_sweep` background loop archives overdue terminal invoices via a `meta.archived_at` marker and, for the WORM `audit_log` class, verifies shipment instead of deleting — it never deletes audit rows (composes with the immutability trigger). See `docs/retention.md`.

## Dispatch modes

Extraction, ERP push, and audit logging support two execution modes:
- **local** (default) — in-process. See the loop rule below for *how*.
- **lambda** — sends message to SQS, processed by Lambda handler

Files: `*_dispatch.py` (router), `*_lambda.py` (Lambda handler).

**In `lambda` mode the SQS send is offloaded, not inlined.** boto3 is
synchronous: building the client resolves the credential chain (which can reach
IMDS) and `send_message` is a full HTTPS round trip. All three `_send_to_sqs`
helpers are reached from an `async def` — `dispatch_extraction` from the invoice
upload route AND the public email-intake webhook, `dispatch_erp` from the ERP
send path, `dispatch_audit` / `dispatch_auth_audit` from every audited mutation
including **every login attempt** — so each call site hands it to
`asyncio.to_thread`, the same arrangement `services/storage` and the
audit-shipping adapters use. `tests/test_sqs_dispatch_nonblocking.py` is the
drift guard (a thread-identity assertion per entry point plus an AST scan that
fails on a bare `_send_to_sqs(...)` inside any coroutine in those modules).

### The event-loop rule (read before adding a dispatcher)

`app/database.py`'s `control_engine` and `_tenant_engines` belong to the event
loop that first drives them — in a running app, uvicorn's. **An asyncpg
connection cannot cross event loops**, and the failure is not contained: it
raises `RuntimeError: got Future attached to a different loop` *and* can return
the half-used connection to the pool the **request path** draws from, after
which unrelated endpoints hang on it. That is exactly how a broken
`payment_erp_sync` presented — as `PATCH /api/organization` timing out, in e2e
specs that had already passed their own assertions.

What makes this easy to reintroduce: a dispatcher creating its own engines
(they all did) is **not sufficient**. `transition_invoice` fires notification,
audit and webhook hooks, and each opens its *own* control-plane session — and
`dispatch_audit` its own tenant engine — by reaching for the module global.
That is code a dispatcher never calls directly and cannot hand a session to.

So there are exactly two correct shapes:

| Shape | When | Who |
|---|---|---|
| `asyncio.create_task` on the caller's loop | the work is `await`-only I/O | `erp_dispatch`, `payment_erp_sync` |
| worker thread + own loop, wrapped in `database.dispatch_engine_scope(...)` | the work genuinely blocks | `extraction_dispatch` |

`extraction_dispatch` keeps its 3-worker pool because extraction runs PyMuPDF
rendering and Tesseract OSD — synchronous CPU work that would stall the request
loop — and because the pool doubles as the concurrency limiter that keeps bulk
uploads under the AI providers' rate limits. It declares its loop-local engines
once via `dispatch_engine_scope`; every `control_session_factory()` /
`get_tenant_engine()` underneath then resolves to those instead of the globals,
with no per-call-site change. Engines the scope creates itself are disposed on
exit; ones passed in are the caller's to dispose.

`control_session_factory` is therefore a **function**, not the
`async_sessionmaker` it used to be — every call site already spelled it
`control_session_factory()`, so nothing changed for callers. Code that needs to
rebind the underlying sessionmaker (the pytest harness's
`.configure(bind=...)`) must target `_default_control_session_factory`.

Fire-and-forget tasks are held in a module-level set: `asyncio` keeps only a
weak reference to a running task, so one with no other referent can be
collected mid-await and vanish silently.

Guarded by `tests/test_dispatch_engine_scope.py` (the indirection reaches
`notification_dispatch` / `audit_dispatch` without either being edited, and a
worker's scope never leaks into the request context) plus the loop-identity
tests in `tests/test_erp_dispatch.py` and `tests/test_payment_erp_sync.py`.

## Authentication (`api/deps.py`)

- JWT HS256 signed with `FEOH_SECRET_KEY`, 30-min expiry (configurable)
- Token payload: `sub` (user_id), `org` (org_id), `jti` (unique ID for blocklist)
- `get_current_user()` — FastAPI dependency, returns User or 401
- Logout adds `jti` to Redis blocklist with TTL matching token expiry
- `get_current_user` also stashes the requesting `jti` on `user.session_jti` (transient, like `effective_permissions`) — that's what lets the session routes mark the caller's own entry and spare it from "sign out everywhere else"

### Sessions (`services/session_management.py`, `app/redis.py`)

Each sign-in registers its JTI in `active_jtis:<user_id>` (sorted set, scored by issue time) plus a companion metadata hash `session_meta:<user_id>` (IP, coarse device label from the pure `describe_user_agent`, sign-in method). The two are always mutated together, so a session's metadata can never outlive its membership. Beyond the concurrent cap (`FEOH_MAX_CONCURRENT_SESSIONS`) and the forced logout on role change / password reset / deactivation, this backs:

- `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{jti}`, `POST /api/auth/sessions/revoke-others` — the account holder's own remedy for a leaked token. Every op is keyed on `user.id`, so membership in the caller's set IS the authorization; a foreign JTI is the same opaque 404 as an unknown one. No step-up (they only remove access). Audited `auth.session.revoked`, PII-free.
- `POST /api/admin/users/{id}/revoke-sessions` — standalone admin force-logout (`user.manage`, org-scoped, idempotent, audited `user.sessions_revoked`).

`list_sessions` prunes entries whose token already expired (the set's TTL is refreshed on every login, so it can outlive the tokens inside it) without blocklisting them — there is nothing left to revoke. The **raw** User-Agent is never stored or logged. See `../docs/authentication.md` § Session management.

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

- TOTP (pyotp) + email-OTP backup + **WebAuthn/passkeys** (`py_webauthn`). Master switch `FEOH_MFA_ENABLED` (default `false` for local dev) gates all three.
- Per-user TOTP secret on `User.mfa_secret`; org-wide enforcement via `Organization.settings.mfa.required`.
- **Enrollment is two-phase and never disturbs a live factor.** `POST /api/auth/mfa/enroll` (and the portal twin) mints a *candidate* secret into Redis (`mfa:pending_enroll:<user_id>` / `mfa:vendor_pending_enroll:<id>`, `FEOH_MFA_ENROLL_PENDING_TTL_SECONDS`, default 900s) — `mfa_secret` / `mfa_enabled` / `mfa_enrolled_at` are written ONLY by `/mfa/enroll/verify`. **Changing an existing factor is a step-up**: enroll-start, `POST /api/auth/mfa/passkey/register`, `DELETE /api/auth/mfa/passkey/{id}` and `POST /api/auth/mfa/disable` take an optional `{password?, code?, assertion?}` body and require one to check out whenever a factor is already live — the shared `pwd_context` password or a code from the CURRENT authenticator (both via the pure `mfa.step_up_verified`), or a **WebAuthn assertion** from an already-registered passkey (`api/auth._step_up_satisfied`, which needs the DB + Redis so it can't live in the pure helper). A "live factor" is an enabled TOTP secret OR any registered passkey, so both doors are gated symmetrically; a genuinely first factor needs none. The assertion is the proof an **SSO-only** account uses — with no password and no TOTP it would otherwise be locked out of its own factor management (it is never *exempted*; exempting would let a stolen JWT plant an attacker-controlled passkey). Its challenge comes from `POST /api/auth/mfa/step-up/passkey {operation}` and is **purpose- and operation-bound**: login and step-up challenges live in different single-use Redis slots (`webauthn:auth_challenge:<uid>` vs `webauthn:stepup_challenge:<operation>:<uid>`), so a step-up assertion can't mint an access token, a login assertion can't authorize a factor change, and a `passkey_register` assertion can't authorize a `passkey_delete` — the only thing separating the two ceremonies is the challenge, since `clientDataJSON.type` is `webauthn.get` for both. `purpose` is a required kwarg on `begin_authentication`/`finish_authentication`. The supplier portal has no passkeys (`WebAuthnCredential` → control-plane `users.id`, `VendorUser` is tenant-scoped), so `/portal/auth/mfa/*` stays password-or-code. Every step-up is throttled 5/min **per account** (`_throttle_step_up`, not per-IP — the attacker holds the token) and writes a PII-free `auth.mfa.step_up.failure` / `portal.mfa.step_up.failure` audit row on failure; `/mfa/disable` on both surfaces rides the same helpers. Without all of this, a leaked access token alone could strip or swap the second factor, silently and unthrottled.
- **Passkeys are a separate code path** (`services/webauthn.py`), additive + opt-in. Credentials live in the control-plane `webauthn_credentials` table (`WebAuthnCredential`, migration 0063, in `CONTROL_TABLES`) — one row per registered authenticator, keyed by `user_id`. Register/list/delete + authenticate endpoints under `/api/auth/mfa/passkey/*`, plus the step-up-challenge endpoint `POST /api/auth/mfa/step-up/passkey`; the authenticate ceremony is gated by the login-issued MFA challenge token (public, pre-access-token), register/list/delete and step-up-start require JWT. The per-ceremony challenge is stashed single-use in Redis (`webauthn:reg_challenge:<user_id>`, `webauthn:auth_challenge:<user_id>`, `webauthn:stepup_challenge:<operation>:<user_id>`); the signature counter is verified + bumped (clone-detection). RP ID / origins configurable (`FEOH_WEBAUTHN_RP_ID` / `FEOH_WEBAUTHN_ORIGINS`; dev defaults `localhost` / `http://localhost:7777`). Public key + counter are not secret in the password sense and never logged.
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
  `FEOH_SAML_SP_*` via sops. Local IdP: Keycloak (`pnpm saml:seed`).
- Full reference: `../docs/authentication.md` § SAML SSO + `../docs/local-sso-saml.md`.

## Organization settings (JSONB)

Stored in `Organization.settings`:
```json
{
  "company": { "name", "tax_id", "address", "phone", "website", "logo_url",
               "vat_registration_number", "companies_house_number" },
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

Canonical roster: `services/exception_lifecycle.EXCEPTION_TYPES` — `duplicate`, `po_mismatch`, `fraud_flag`, `extraction_failed`, `unverified_vendor`, `review_rejected`, `amount_exceeded` (legacy — no longer raised, kept for historical rows), `missing_data`, `quality_hold`, `price_variance`, `contract_noncompliant`, `erp_reconciliation`, `line_total_mismatch`, `payment_compliance_hold`. The `exception_type` column is a plain `String(50)`, so that tuple — not a DB enum — is the source of truth; `api/exceptions.EXCEPTION_TYPE_LABELS` must cover it exactly, and `tests/test_exception_type_labels.py` AST-scans `app/` to fail when a type is raised without joining the roster and getting a label.

**Every lifecycle event is audited.** `services/exception_lifecycle` is the single chokepoint: `create_exception` writes `exception.raised`, and the human queue (`api/exceptions`), the agent coordinator, and the compliance-hold release/dismiss path (`api/payments.py::_resolve_compliance_hold_exception`) all resolve/escalate/dismiss through `record_decision`, which writes `exception.resolved` / `.escalated` / `.dismissed` (+ `exception.assigned` on routing). Rows are correlation-keyed to the **invoice**, so they land on its SOX trail; an invoice-less exception self-correlates on its own id. `details` is PII-lean and carries a `payment_blocking` flag derived from `api/payments.PAYMENT_BLOCKING_EXCEPTION_TYPES` itself — clearing a `duplicate` / `fraud_flag` / `line_total_mismatch` is the human sign-off that lets a payment run proceed, and the mutable `exceptions` row (single-valued, not WORM-shipped, no append-only trigger) can't be that record. Escalation records the note but never stamps `resolved_by` / `resolved_at`. Every `/api/exceptions` mutation is entity-scoped like the reads. See `docs/exception-lifecycle.md`.

Severity: `error`, `warning`, `info`. Auto-detected by `invoice_warnings.py`. `erp_reconciliation` is opened by the ERP webhook (`api/erp_webhook.py`) when the ERP reports an invoice VOIDED/CANCELLED that we already advanced past the point where `→ failed` is a legal transition (`sent_to_erp` / `posted_in_erp` / `payment_scheduled` / `paid`) — money may be in flight, so it is flagged for human reconciliation instead of auto-transitioned (idempotent per open exception, PII-free description). `payment_compliance_hold` is opened by `api/payments.py` whenever `_execute_single_payment` parks a payment at `pending_compliance` (no screenable vendor, or the sanctions/KYC adapter itself returns a `hold` verdict) — dedup'd per `(invoice_id, "payment_compliance_hold", "open")` so a retried execution never double-opens it, and resolved by `POST /api/payments/{id}/compliance/release` or `/dismiss`. See `backend/docs/payments.md` § Sanctions / compliance hold resolution.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/seed.py` | Creates 2 tenants (acme, techflow) with full sample data (vendors, invoices, POs, payments, exceptions) + a `WorkflowInstance`/`WorkflowStep` per invoice (so the approval queue + assistant pending-approvals tool aren't empty — `ready_for_review` invoices get an active approval step assigned to the org admin) + calls `seed_extras` so contracts / credit memos / discount offers / expenses are populated too |
| `scripts/seed_extras.py` | Additive, idempotent per-tenant seed for the contract (`/contracts`), credit-memo (`/credit-memos`), discounting (`/discounts`) and expense (`/expenses`) pages. `seed_extras(session, org_id)` is reused in-line by `seed_tenant`; the CLI (`--tenant feoh_acme`) tops up an already-seeded tenant without a wipe. Skips if the tenant already has contracts. |
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

The welcome email contains the tenant URL (`FEOH_TENANT_URL_TEMPLATE`, e.g. `https://{slug}.app.com`) and a 16-char URL-safe temp password. The user is forced to change it on first login (`User.must_change_password` is `true` until they hit `/api/auth/change-password`).

**Pluggable services:**

- `services/email_adapters/` — `console` (local dev, logs to stdout) and `ses` (AWS SES) via `FEOH_EMAIL_PROVIDER`. Same registry pattern as extraction/ERP adapters.
- `services/tenant_provisioning.py` — reusable async `provision_tenant()` used by both the CLI and the API.
- `services/rate_limit.py` — Redis sliding-window limiter, keyed on `(endpoint, subject)` where `subject` defaults to client IP but can be an explicit value (e.g. email). Signup uses three limits: per-IP `/start` + `/complete` (`FEOH_SIGNUP_RATE_LIMIT_PER_HOUR`, default 5), per-email `/start` (`FEOH_SIGNUP_EMAIL_RATE_LIMIT_PER_HOUR`, default 3, anti email-bombing), and per-IP `/slug-check` (`FEOH_SLUG_CHECK_RATE_LIMIT_PER_HOUR`, default 120, anti-enumeration).
- `utils/slug.py` — regex + reserved-word blocklist + DB uniqueness check.
- `utils/hcaptcha.py` — server-side siteverify. Skips when `FEOH_HCAPTCHA_SECRET` is empty (local dev).
- `utils/passwords.py` — `generate_temp_password()` + `validate_password_complexity()` (min 12 chars, upper/lower/digit).

The captcha sitekey is exposed to the frontend via `GET /api/public-config` so the SvelteKit build doesn't need to bake it in.

Relevant env vars: `FEOH_ENVIRONMENT` (deployed envs refuse to boot with an empty `FEOH_HCAPTCHA_SECRET`), `FEOH_EMAIL_PROVIDER`, `FEOH_EMAIL_FROM`, `FEOH_AWS_SES_REGION`, `FEOH_PUBLIC_URL`, `FEOH_TENANT_URL_TEMPLATE`, `FEOH_HCAPTCHA_SECRET`, `FEOH_HCAPTCHA_SITEKEY`, `FEOH_SIGNUP_RATE_LIMIT_PER_HOUR`, `FEOH_SIGNUP_EMAIL_RATE_LIMIT_PER_HOUR`, `FEOH_SLUG_CHECK_RATE_LIMIT_PER_HOUR`.

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
  `[tool.ruff.format] exclude = ["*.md"]` keeps the formatter on Python source:
  ruff 0.16 started formatting Python blocks embedded in Markdown, and the docs'
  snippets are illustrative (elided `...` bodies, comment alignment used for
  emphasis) rather than runnable. Lint rules are unaffected.
- **Schemas** — Pydantic v2 models in `app/schemas/` for all request/response types.
- **No dotenv in Lambda paths** — `main.py` imports dotenv for local dev; Lambda entry points must not.
- **Tenant isolation** — always resolve tenant via dependency injection (`get_tenant_db()`), never hardcode DB names.
- **Row locking** — use `get_invoice_for_update()` for any status transition to prevent race conditions.
