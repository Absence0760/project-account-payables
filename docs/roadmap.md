# Roadmap

Feature backlog for the AP automation platform, ordered by impact.

## Legend

- **Done** — implemented and working
- **Partial** — backend or models exist, needs completion
- **Planned** — not started

---

## Priority 1: Core Automation (highest impact)

### Real AI Extraction
**Status:** Done — adapter pattern with Claude Vision (platform), OpenAI GPT-4V, AWS Textract (BYOK). Platform/BYOK dual model. See [ai-extraction.md](../backend/docs/ai-extraction.md).

- [x] Extraction adapter pattern with dispatcher
- [x] Claude Vision adapter (platform default) with structured JSON prompt
- [x] OpenAI GPT-4V adapter (BYOK)
- [x] AWS Textract adapter (BYOK)
- [x] Mock adapter for development
- [x] Per-field confidence scoring (0-1)
- [x] Extract line items (not just header fields)
- [x] Platform/BYOK dual model — platform keys in env vars, customer keys in org settings
- [x] Extraction config in organization settings UI
- [x] Usage tracking (ExtractionUsage model) for billing
- [x] Support multi-page PDFs — `_pdf_to_images()` converts all pages (up to 20) to PNG; vision adapters (Ollama, OpenAI) send all page images in one API call. Claude Vision handles multi-page natively via document mode. Text-mode already extracted all pages.
- [x] Handle rotated scans — Tesseract OSD auto-rotation in `app/services/image_preprocess.py`, called from `OllamaAdapter._pdf_to_images`. Gated on `AP_EXTRACTION_AUTO_ROTATE` (default on); soft-depends on `pytesseract` + the `tesseract` binary — missing deps silently no-op. Small-angle deskew (1–5° tilt) and low-quality enhancement still open if real data demands them.
- [x] Auto-approve extraction above configurable threshold — `auto_approve_enabled` + `auto_approve_threshold` on extraction step config; also checks `auto_approve_below` from approval step. Invoices skip review and go directly to `approved` with `approved_by="system (auto-approve)"`
- [x] Custom chart of accounts in extraction prompt — org's active GLAccount rows queried and injected into extraction prompt via `config["gl_account_catalog"]`. Falls back to hardcoded default list
- [x] Extraction self-correction pass — `services/extraction_self_correction.py` verifies arithmetic (subtotal+tax≈amount), date ordering, line-item math. Violations lower confidence (-0.2) and add warnings. Controlled by `org_settings.extraction.self_correction_enabled`
- [x] Learning from corrections — per-vendor correction cache (see below)
- [x] RAG-based extraction priors — pgvector + few-shot retrieval (see below)
- [x] Semantic duplicate detection — near-duplicate catch via cosine similarity on the same `invoice_embeddings` store. Threshold `AP_DUPLICATE_SIMILARITY_THRESHOLD` (default 0.95, tighter than RAG retrieval). See `backend/docs/ai-extraction.md` § Duplicate detection.
- [x] Stuck-extraction reaper — `services/extraction_reaper.py` sweeps every 60s (configurable) and transitions invoices in `pending` longer than `AP_EXTRACTION_TIMEOUT_SECONDS` to `failed`. Started in `main.lifespan`; one-shot CLI at `scripts/reap_stuck_extractions.py`.

**Files:** `backend/app/services/extraction_adapters/`, `backend/app/services/extraction.py`, `backend/app/services/vendor_priors.py`

#### Learning from corrections — per-vendor cache (shipped)

When a reviewer corrects extracted fields during approval, the corrected values are stored keyed by `(vendor_id, field_name)` in the `vendor_extraction_priors` tenant table. On the next extraction for the same vendor, low-confidence values for cached fields are overlaid with the stored values. Only "vendor-consistent" fields are cached (currency, tax_rate, payment_terms, payment_method, vendor_address, vendor_tax_id, remit_to_address, gl_account, cost_center) — never per-invoice fields like amount or invoice_number.

This is deterministic and requires no ML infrastructure. It handles the 80% case ("same vendor's invoices follow the same pattern") with zero cold-start cost.

#### RAG with pgvector (shipped)

Semantic-similarity learning complementing the per-vendor cache. At correction time, the invoice's PyMuPDF-extracted text is embedded with `text-embedding-3-small` (mock adapter available for local dev) and stored in `invoice_embeddings` (pgvector) alongside the final corrected fields. At extraction time, the incoming invoice's text is embedded, top-3 semantic neighbors are retrieved via cosine distance, and the matched `(invoice_text, corrected_fields)` pairs are injected into the Claude Vision prompt as few-shot examples.

Tenant-scoped by default (no cross-tenant leakage). HNSW index on the embedding column for approximate nearest-neighbor search at scale. Metadata about which neighbors were used is persisted on `InvoiceExtractionResult.priors_metadata` and surfaced in the invoice detail UI via `GET /api/invoices/{id}/priors`.

Conflict resolution: the per-vendor cache (see above) runs AFTER the AI output and overrides low-confidence fields, so when cache and RAG disagree on a field, the cache wins — per-vendor explicit corrections are more authoritative than semantic retrieval.

---

### 2/3-Way PO Matching & Auto-Validation
**Status:** Done — matching runs after every extraction and on every invoice mutation. Mismatches and missing POs route into the exception queue. Modal renders a PO Match panel with status, variance, and issues. PO + GR management UIs and adapter-driven ERP sync are live.

- [x] 2-way match: invoice vs. PO (amount, vendor)
- [x] 3-way match: invoice vs. PO vs. goods receipt (quantity received)
- [x] Configurable tolerance thresholds (default 5%)
- [x] Vendor-aware matching (PO lookup by vendor_id)
- [x] Wired into extraction/review pipeline — `services.invoice_warnings.refresh_warnings` runs `match_invoice_to_po` whenever an invoice changes; result is persisted on `invoice.po_match` (JSONB column added in migration 0006)
- [x] Match result display in invoice modal — color-coded panel (matched / mismatch / partial / no PO) with PO #, variance, issues
- [x] Routes mismatches to exception queue — `po_mismatch` exceptions auto-created, severity scaled (error for missing PO, warning for amount variance, info for partial 3-way receipt)
- [x] PO management UI — list page with status chips + search + Sync from ERP toolbar action; click-through detail modal showing line items and linked invoices (matches by `po_number`)
- [x] Goods receipt UI — `/goods-receipts` list page with received-date / status / line-count columns; detail modal shows linked PO + line items received; backend GET endpoints support `?po_id=` and `?status=` filters
- [x] PO sync from ERP — `POST /api/purchase-orders/sync-erp` now dispatches via `get_erp_adapter().list_pos()`. Mock adapter ships a deterministic three-PO catalogue; Merge.dev adapter walks paginated `/purchase-orders` and maps to the unified `PoPayload`; NetSuite + Business Central inherit the base's `[]` default until those endpoints are wired (sync no-ops rather than 500s)

**Files:** `backend/app/services/po_matching.py`, `backend/app/models/procurement.py`

---

### AI Auto GL Coding
**Status:** Done. Claude Vision suggests GL code + cost center, constrained to the org's active chart of accounts; cached vendor priors and RAG-retrieved approved invoices both feed the prompt; suggestions are validated post-extraction; admins can backfill via bulk re-code.

- [x] AI suggests GL code + cost center during extraction
- [x] Confidence score per suggestion
- [x] Auto-apply above 0.7 threshold
- [x] Learn from corrections — reviewer corrections to `gl_account` / `cost_center` feed the per-vendor correction cache (see AI extraction section). Future extractions for the same vendor overlay the cached code on low-confidence suggestions.
- [x] Custom chart of accounts per org in the prompt — `services.extraction.run_extraction` queries the org's active `GLAccount` rows and injects them via `config["gl_account_catalog"]`; the Claude Vision adapter swaps the `{{GL_ACCOUNT_CATALOG}}` placeholder. Falls back to a static default list when the org hasn't synced a chart yet.
- [x] RAG-driven GL coding — `services.rag.retrieve_similar` fetches nearest-neighbor approved invoices via `invoice_embeddings`; `SNAPSHOT_FIELDS` includes `gl_account` so the few-shot prompt prepended to extraction surfaces the historical code. New vendors whose layout resembles a known one inherit GL signal from the neighbor.
- [x] Bulk re-code capability — `POST /api/invoices/bulk-recode-gl` (admin-only). Date / vendor scoped; priors-first then optional AI fallback. Defaults to `dry_run=true` and returns a `{matched, would_change, by_source, skipped, changes}` report. Admin UI: "Bulk Re-code GL" button on `/invoices` opens a preview-then-apply modal. Audit-logs each persisted change as `invoice.gl_recoded`.
- [x] GL code validation against chart of accounts — post-extraction guard in `run_extraction` rejects any AI-suggested code (or cached vendor prior that's gone stale) that isn't in the org's active chart, drops it from the invoice header and line items, and emits a structured `gl_account_invalid` warning. No-ops when the org hasn't synced a chart yet.

### Recurring / Subscription Invoices
**Status:** Planned

Predictable, fixed-cadence spend (rent, SaaS seats, utilities, insurance) shouldn't need a fresh upload + extraction every period. A recurring template auto-generates the next invoice on schedule, pre-coded and pre-matched, so it lands straight in the approval queue. Common in Bill.com, Tipalti, and Stampli; absent here today.

- [ ] `RecurringInvoiceTemplate` tenant-scoped model — vendor, amount (or amount source), GL coding, entity, cadence (RRULE-ish: monthly / quarterly / annual + day-of-period), start/end, next-run-at; new Alembic migration that fans out to every tenant
- [ ] Background generation sweep — mirror the existing `contract_renewal` / `discount_auto_trigger` loop pattern (`AP_RECURRING_INVOICES_ENABLED` master switch, off in local dev); generates the next `Invoice` in `new`/`pending` and advances `next_run_at`. **Idempotent** on `(template_id, period_key)` so a double-fire never double-creates
- [ ] Variance handling — flag when an arrived invoice for a recurring vendor deviates from the template amount beyond a tolerance (reuse the price-variance signal from data enrichment) rather than blindly trusting the schedule
- [ ] Link generated invoices back to their template + a "skip / pause / end" control on the template; every generation + lifecycle change audited
- [ ] Frontend `/recurring` route — template CRUD, upcoming-schedule preview, generated-invoice history

**Competitors:** Bill.com (recurring bills), Tipalti (subscription spend), Stampli, Airbase (SaaS spend management)

---

## Priority 2: Workflow, Approvals & Exceptions

### Exception Queue
**Status:** Done

Dedicated page for handling flagged invoices — mismatches, rejections, anomalies.

- [x] Exception list page with filters (type, status, severity, date)
- [x] Exception types: PO mismatch, duplicate, fraud flag, rejection, extraction failure, unverified vendor, missing data
- [x] Resolution actions: resolve, escalate, dismiss (with resolution note)
- [x] Summary: counts by status, breakdown by type
- [x] Link back to invoice from exception
- [x] Assignment — `POST /api/exceptions/{id}/assign` (org-scoped user lookup); auto-assignment from `Organization.settings.exceptions.auto_assign_by_type` at creation; `?assigned_to_user_id` filter on list
- [x] SLA tracking — `due_at` (sla_hours_by_type / default_sla_hours) + `is_overdue` flag in list payload; `time_to_resolution_hours` populated only on terminal transitions (resolve / dismiss)
- [x] Bulk resolution — `POST /api/exceptions/bulk/resolve` with partial-success contract `{updated, skipped:[{id, reason}]}` matching the invoice bulk endpoints

**Files:** `backend/app/api/exceptions.py`, `backend/app/models/exception.py`, `backend/alembic/versions/0013_exception_assignment_sla.py`, `backend/tests/test_exception_assignment.py`, `frontend/tests-e2e/exceptions/assign-bulk.spec.ts`

---

### Advanced Approval Routing
**Status:** Partial — **Competitive gap: all competitors have this**

Current state: manual, specific, auto, and chain approval strategies. Amount-based auto-approve, CFO gate, max-amount rejection, multi-level chains, segregation of duties, and delegation are implemented. No escalation, email/Slack approval, or visual matrix builder yet.

- [x] Amount-based auto-approve (auto_approve_below threshold)
- [x] CFO role gate (require_cfo_above threshold)
- [x] Max invoice amount rejection (max_invoice_amount)
- [x] Multi-level approval chains (strategy="chain", ApprovalLevelConfig)
- [x] Segregation of duties (require_segregation, uploaded_by_id)
- [x] Delegation / out-of-office (delegate_to_id, delegate_until, /api/auth/delegation)
- [x] Department/GL-based routing — `ApprovalLevelConfig.routing_rules` filters by gl_account / cost_center / department / vendor_id; AND-composes with min/max amount; unknown fields fail open so a stale UI config can't lock the chain
- [x] Parallel approvals — `parallel_mode: "any" | "all"`. `any` = `required_approvals` distinct users (default, legacy behaviour). `all` = every listed approver_id must approve.
- [x] Escalation rules — `escalation_hours` + `escalation_to_user_ids` per level. Background sweeper (`services/approval_escalation.py`) appends the targets onto the level's `approver_ids` once the level's `entered_at` is older than `escalation_hours`. Idempotent. Toggleable via `AP_APPROVAL_ESCALATION_ENABLED`.
- [ ] Email approval — approve/reject directly from email notification without logging in *(skipped — needs SMTP / signed-token credentials)*
- [ ] Slack/Teams approval — approve/reject from Slack message buttons *(skipped — needs Slack/Teams app + webhook secret)*
- [x] Approval matrix UI — `frontend/src/lib/components/ApprovalMatrixEditor.svelte` plugged into `/workflows/[id]` when `approver_strategy=chain`. Edits levels (name, amount range, parallel mode, approvers, routing rules, escalation hours + targets); persists through the existing `PATCH /api/workflows/{id}` path.

**Competitors:** Coupa (matrix approval), Tipalti (parallel + Slack), Stampli (email/Slack), Airbase (Slack-native), Basware (conditional chains)

---

### Backend RBAC Enforcement
**Status:** Done — `require_roles(*roles)` dependency, full permission matrix applied across every router. Coverage gate in `tests/test_rbac.py` blocks regressions.

- [x] `require_roles(*roles)` dependency in `app/api/deps.py` — any-of semantics, 403 on miss, WARN-level log on denial
- [x] Endpoint-level permission mapping for all 4 roles (admin / ap_manager / ap_clerk / cfo) — see `docs/authentication.md` § RBAC
- [x] Return 403 Forbidden (not just hide UI elements)
- [x] Unit tests for `require_roles` semantics + coverage gate that fails CI if a new endpoint ships without an auth dependency
- [x] Log unauthorized access attempts at WARN level (sufficient for monitoring; persistent audit-log entries deferred to SOC 2 prep)
- [x] Segregation of duties enforcement (approver ≠ creator) — default-on baseline; `check_segregation` runs on every `approve_invoice` call. Opt-out per workflow via `require_segregation: false` for single-operator orgs
- [x] Per-org custom roles — `Role.organization_id` nullable (NULL = system, non-NULL = org-scoped). Admin CRUD at `/api/admin/roles` (POST / PATCH / DELETE) refuses to touch system rows and rejects creation under reserved names. Frontend surface at `/admin/roles` with a system / custom split.

**Files:** `backend/app/api/deps.py`, every `backend/app/api/*.py` router, `backend/tests/test_rbac.py`

---

### Enhanced Fraud Detection
**Status:** Done

Eight fraud rules implemented in `services/invoice_warnings.py`, each
gated by a per-org tunable. Rules raise both an inline warning on the
invoice and an `Exception` row when the signal is actionable. Defaults
in `DEFAULT_FRAUD_RULES`; org admins override via the Fraud Detection
section on `/organization` (UI maps onto `settings.fraud_rules`).

- [x] Semantic duplicate detection — cosine similarity on `invoice_embeddings` catches near-duplicates the exact-match rule misses. See `backend/docs/ai-extraction.md` § Duplicate detection.
- [x] Vendor bank account / remit-to change — flags when an invoice's `remit_to_address` differs from prior approved invoices for the same vendor.
- [x] LLM-based anomaly detection — feeds the invoice + last N approved invoices to the configured extraction provider with a "in-pattern for this vendor?" prompt; opt-in (`llm_anomaly_enabled=False` by default; one LLM call per incoming invoice). Module: `services/llm_fraud_detection.py`.
- [x] Statistical amount anomaly — fires when `amount > vendor_mean + N·σ` over the vendor's prior approved invoices. N + min-history are tunable.
- [x] Rush payment pattern — `due_date - invoice_date <= rush_payment_max_days`.
- [x] New vendor + large amount — vendor age < `new_vendor_max_age_days` AND amount ≥ `new_vendor_large_amount`.
- [x] Personal email domain — flags vendors whose contact email matches a configurable allowlist of free-mail providers.
- [x] Configurable fraud rules per org — `Organization.settings.fraud_rules` takes a partial override; unknown keys are dropped silently so we can ship new rules without a settings migration. Frontend editor at `/organization` Fraud Detection card.

**Files:** `backend/app/services/invoice_warnings.py`, `backend/app/services/llm_fraud_detection.py`, `backend/app/api/organization.py:get_fraud_rule_defaults`, `frontend/src/routes/organization/+page.svelte` (Fraud Detection card), `backend/tests/test_fraud_rules.py` (23 tests), `backend/tests/test_llm_fraud_detection.py` (19 tests), `frontend/tests-e2e/organization/fraud-rules.spec.ts` (7 specs).

---

## Priority 3: Payments

### Payment Run UI
**Status:** Done (core flow) — queue → select + per-row method → create draft → review in modal → execute. Drilldown from Runs tab works.

- [x] Payment queue page — approved invoices sorted by due date, overdue highlighting
- [x] Payment history — all methods in one table (ACH, wire, check, card badges)
- [x] Payment runs list — batch history with status, total, count
- [x] Summary bar — total paid, pending, queue count, payments, rebates earned
- [x] Payment queue backend — `GET /api/payments/queue` and `GET /api/payments/summary`
- [x] Create payment run — select invoices in the queue, choose method per row, totals shown
- [x] Run detail modal — status, total, payments table, references; opens after creating a draft and from any row in the Runs tab
- [x] Execute payment run — separate from create, so a draft can be reviewed before money moves
- [x] Early-pay discount highlighting with savings calculation — queue surfaces discount banner + chip column when `PaymentSchedule.discount_date` is in window
- [x] Void/cancel payment capability — `POST /api/payments/{id}/void` (RBAC: admin/manager), adapter-level void with `voided` status + audit row
- [x] Cancel a draft run before executing — `POST /api/payments/runs/{id}/cancel` releases the invoices back to `ready_for_review`
- [x] Payment remittance generation (PDF/email to vendor) — `GET /api/payments/{id}/remittance` returns reportlab-rendered PDF
- [x] Approval workflow on a draft run (CFO sign-off before execute) — `requires_cfo_approval` gate + `POST /api/payments/runs/{id}/approve` (CFO-only)

**Files:** `backend/app/api/payments.py`, `backend/app/models/payment.py`, `backend/app/services/remittance_pdf.py`, `frontend/src/lib/components/RunDetailModal.svelte`

**See also:** [payments.md](../backend/docs/payments.md)

---

### Virtual Card Program
**Status:** Partial — adapter pattern (Lithic + Nium), models, API endpoints, org config UI, and webhook handler done. Frontend card list page and payment run integration not yet built.

Generate single-use virtual cards per invoice payment. Earn 1-2% rebates on every card payment. Primary monetization channel. See [virtual-cards.md](../backend/docs/virtual-cards.md) for full design.

- [x] VirtualCard and CardRebate data models
- [x] Card adapter pattern with dispatcher (Lithic for US/UK/EU, Nium for global)
- [x] Lithic adapter — card creation, detail retrieval, cancellation, status
- [x] Nium adapter — same interface for 40+ countries
- [x] Mock adapter for development/testing
- [x] Card API endpoints — generate, list, details, cancel, webhook, rebates, dashboard
- [x] Card detail security — role-restricted (admin/manager), audit-logged
- [x] Webhook handler — process charge/settlement events, auto-create rebates
- [x] Platform/BYOK dual model — platform keys in env vars, customer keys in org settings
- [x] Card config in organization settings UI — region auto-selects provider
- [x] Vendor `accepts_virtual_cards` field
- [x] Card list in payments page — dedicated Cards tab + card_last_four/card_provider join on history rows
- [x] Card generation in payment run — `execute_payment_run` calls `card_issuance.issue_card_for_invoice` when `method == "virtual_card"`
- [x] Vendor email notification — pluggable email adapter sends single-use reveal URL on issuance
- [x] Rebate dashboard — monthly earnings + YTD totals block on payments page
- [x] Supplier portal integration — `GET /portal/cards/{token}` returns full card detail once (sha256-hashed token, 7-day expiry)

### International Payments
**Status:** Complete — see `backend/docs/international-payments.md`. Migrations 0017 + 0018. Sanctions provider integration today ships as a `complyadvantage` skeleton with the wire shape correct; the live API key needs to be set in `Organization.settings.compliance.sanctions.api_key`. Wise / Tipalti payment-rail adapters slot in via `@register_payment_adapter` — Modern Treasury covers most demand for now.

- [x] Multi-currency payment execution — pay in vendor's local currency (`services/international_payments.py::prepare_international_payment` builds the Payment row with `source_currency`, `source_amount`, `fx_rate`, `fx_locked_at`, `corridor`, `target_country`)
- [x] FX rate management — real-time rates, rate lock at payment creation. Pluggable adapter (`services/fx_adapters/`) — mock + Open Exchange Rates today; Wise / Tipalti slot in via `@register_fx_adapter`
- [x] Cross-border ACH (NACHA Global ACH / IAT) — `method=international_ach` for USD→CA / MX / GB / BR / select LATAM corridors; cheaper than SWIFT for low-value recurring payments
- [x] International wire transfers (SWIFT) — `method=international_wire`; SWIFT/BIC validation in `utils/banking.py`
- [x] SEPA payments (EU) — `method=sepa`; IBAN mod-97 + SEPA zone membership in `utils/banking.py`; corridor picker auto-routes EUR→SEPA-country to SEPA
- [x] Payment corridor optimization — `services/corridor_quotes.compare_quotes` ranks N processor quotes (cheapest by default; `fastest` mode for urgent runs). Org enables via `payments.providers=[...]`; legacy single-provider config still works
- [x] Regulatory compliance per corridor (KYC/AML) — sanctions / PEP screening (`services/sanctions_adapters/` — mock + ComplyAdvantage skeleton), KYC gating per high-risk corridor, AML trailing-12m-spend signal, append-only `sanctions_checks` audit log
- [x] FX gain/loss tracking — `compute_fx_gain_loss` (booked vs realized); columns persisted on `payments` for reporting

**Competitors:** Tipalti (196 countries, 120 currencies), Coupa Pay, Basware Pay, Airbase

---

### Bank / Payment Processor Integration
**Status:** Done (Modern Treasury + mock) — adapter pattern lives in `backend/app/services/payment_adapters/`. Real ACH/wire/RTP flow works end-to-end (create payment → idempotent processor call → webhook-driven status updates → ERP sync on settle).

- [x] Adapter scaffold (`base.py`, `dispatcher.py`, `mock_adapter.py`)
- [x] Modern Treasury adapter — full payment-order create + status lookup + webhook parsing with HMAC-SHA256
- [x] Per-org config (`Organization.settings.payments`) — provider, credentials, originating account, webhook secret, sandbox flag
- [x] Frontend org-settings UI for selecting + configuring the processor
- [x] Webhook handler (`POST /api/payments/webhook/{tenant_slug}/{provider}`) — drives `submitted → completed/failed` transitions
- [x] `payments.provider`, `provider_payment_id`, `failure_reason`, `submitted_at`, `completed_at` columns (alembic 0007)
- [x] `execute_payment_run` dispatches via adapter; run status reflects rollup (`completed` / `partial` / `submitted` / `failed`)
- [x] Vendor counterparty management UI — `Bank` action on the vendors grid opens a counterparty modal
- [x] Reconciliation job — `services/payment_reconciler.py` sweeps every tenant on a timer, re-polls non-terminal payments, force-fails past `AP_PAYMENT_RECONCILE_MAX_AGE_HOURS`. Disabled by default in local dev.
- [x] Stripe Treasury / Increase / Column adapters — ACH + wire (Stripe Treasury, Increase, Column) via the same `@register_payment_adapter` pattern. Idempotency, HMAC webhooks with replay protection on Stripe + Increase (timestamped signatures), plain HMAC on Column.
- [x] ACH integration — Dwolla adapter (OAuth client-credentials + token caching) for ACH-specialist orgs; ACH also available via Stripe Treasury / Increase / Column for orgs using their other rails too. Plaid bank-link is a separate concern (vendor onboarding, not payment origination) and remains pending.
- [x] Wire transfer integration — Modern Treasury, Stripe Treasury, Increase, Column all support `method=wire`. Domestic and international (SWIFT) wires both flow through the same path (`international_wire` via the corridor picker).
- [x] Check printing service — Checkeeper adapter (`method=check`): prints + mails physical checks. Mailing-address validation refuses checks without a valid US address before submitting.
- [x] Payment status webhooks from processor — every adapter implements `parse_webhook` with HMAC signature verification + Redis-based event dedup (`services/webhook_security.py`). Stripe + Increase use timestamped signatures with 5-min replay protection.
- [x] Bank reconciliation — import statements, auto-match. CSV importer (`services/bank_reconciliation.py::parse_csv_statement`) handles the common bank export formats; the matcher runs three strategies (provider_id → amount+date → fuzzy vendor) with confidence scores 100 / 80 / 50–70. Unmatched debits surface as exceptions. See `backend/docs/bank-reconciliation.md`.

---

### Vendor Statement Reconciliation
**Status:** Planned

Distinct from bank reconciliation (cleared payments ↔ bank lines): this reconciles a **supplier's statement of open items** against our AP ledger to catch missing invoices, double-posted bills, mis-applied credits, and stale balances before month-end close. A core AP-clerk task that's entirely manual today.

- [ ] Statement intake — CSV/PDF upload (reuse the extraction pipeline for PDF statements) parsed into a normalized list of `{invoice_number, date, amount, status}` line items, vendor-scoped
- [ ] Reconciliation engine (`services/vendor_statement_recon.py`, pure) — match statement lines to our `Invoice` rows by invoice number → amount+date fallback; classify each as *matched* / *missing on our side* (supplier billed, we never received) / *missing on their side* (we have it, they don't) / *amount mismatch*
- [ ] Persist a `VendorStatementReconciliation` run + line results (tenant migration, fans out); surface "missing on our side" rows as actionable exceptions feeding invoice intake
- [ ] Frontend reconciliation view — upload, side-by-side diff, per-line resolve; every resolution audited
- [ ] Period close tie-in — block/flag close when a vendor with a material balance has an unreconciled statement

**Competitors:** Tipalti, Basware, Medius (statement reconciliation in close workflows); most SMB tools lack it — a differentiator down-market

---

### Positive Pay / Payment Fraud File
**Status:** Planned

Bank-side fraud control: export an issued-items file so the bank only honors checks/ACH debits we actually originated. A natural extension of the existing `checkeeper` check-printing + payment-rail adapters, and a frequent enterprise-AP procurement requirement.

- [ ] Positive Pay file export (check issue file) — per-bank format (BAI2-ish / fixed-width / CSV) of `{check_number, payee, amount, issue_date, account}` for every check in an executed payment run; pluggable per-bank formatter like the existing payment adapters
- [ ] ACH Positive Pay / debit-block authorization list — export approved originators for ACH debit filtering
- [ ] Exception return handling — ingest the bank's "items presented not on file" report and surface mismatches as fraud exceptions
- [ ] Generation is idempotent per run + audited; account/routing numbers stay out of logs and error bodies (PII invariant)

**Competitors:** Coupa Pay, Tipalti, AvidXchange (positive pay as a treasury-controls feature)

---

## Priority 4: Analytics & Reporting

### Dashboard Enhancements
**Status:** Done — operational metrics live on `GET /api/dashboard`. See `backend/docs/analytics.md`.

- [x] Spend by vendor chart — `vendor_spend` (top 10 by amount, served sorted)
- [x] Invoice aging chart — `aging` (current / days_30 / days_60 / days_90_plus)
- [x] Processing time metrics — `processing_time` (avg + median + p95 days from upload→approval and upload→paid; min-sample threshold collapses to 0 below 5 rows)
- [x] Approval bottleneck detection — `approval_bottleneck` (per-approver pending count, oldest age, average age; unassigned rolls under a synthetic key)
- [x] Monthly trend lines — `monthly_trend`
- [x] Discount capture rate — `discount_capture` (eligible / captured / missed counts + amounts + capture_rate_pct)
- [x] Touchless rate tracking — `touchless_rate`
- [x] Export reports as CSV — `GET /api/analytics/export/{report}` for invoice_register / vendor_spend / payment_register / aging_snapshot. PDF deferred (separate reportlab/weasyprint piece).
- [x] Scheduled report delivery via email — migration 0020 + `scheduled_reports` table + `services/scheduled_reports.execute_schedule`. Daily / weekly / monthly cadence; PII-safe failure messages; auto-disable after 5 consecutive failures.

---

### CFO / Finance-Leader Analytics
**Status:** Done — see `GET /api/analytics/cfo` and `backend/docs/analytics.md`.

Dashboard Enhancements above is *operational* (for AP clerks/managers). CFOs and controllers buy on different metrics — the ones that show up in board decks and drive working-capital decisions. Separate surface because the audience and filter defaults differ (entity, period, currency, accrual vs cash).

- [x] Days Payable Outstanding (DPO) trend — `dpo_current` + `dpo_trend` (last 6 months). Computed AP/COGS×period_days; benchmark overlay deferred until we ship industry-benchmark data.
- [x] Cash conversion cycle — `cash_conversion_cycle`, returns NULL when DSO/DIO unavailable (we're AP-only) so the UI shows "needs receivables data" rather than a misleading 0.
- [x] Accruals view — `accruals.{open_po_amount, received_amount, unposted_invoice_amount, total_accrual}`. `received_amount` is approximated 0 today pending a 3-way-match fan-out — flagged on the response.
- [x] Working capital impact — `working_capital_impact_5_days` (avg_daily_outflow × 5; configurable via days-extended param when called via drill-through).
- [x] Supplier concentration — `supplier_concentration` (top-10 / top-50 share, largest vendor, `flagged` when largest exceeds 25%).
- [x] Fraud rate trend — `fraud_rate_trend` (exceptions per invoice per month, last 6 months).
- [x] Early-pay discount ROI — `discount_capture` on the dashboard surfaces $ captured + $ missed; the CFO endpoint quotes the same numbers.
- [x] Rebate yield — `rebate_yield.{yield_pct, annualised_rebates}` (virtual-card rebates / spend × 100 + 12/months annualisation).
- [x] Forecast variance — `POST /api/analytics/forecast_variance` accepts a CFO-supplied forecast and returns actual vs forecast vs variance vs variance_pct per month. Forecasts are NOT persisted — the CFO pastes from their FP&A tool.
- [x] Drill-through — `/api/analytics/drill/spend_concentration`, `/api/analytics/drill/dpo`. Per-metric drill is the design pattern; new metrics get a new drill endpoint as they ship.

**Competitors:** Coupa (Spend Intelligence), Tipalti (CFO Insights), SAP Ariba (Spend Visibility), AppZen (audit analytics). This is where enterprise AP tools differentiate from SMB tools.

---

## Priority 5: Multi-Currency & Tax

### Multi-Currency Support
**Status:** Done — reporting-currency rollups + locale-aware display, built on the existing FX adapters (`services/fx_adapters/`) and international-payments rate locking. See `backend/docs/multi-currency.md`.

- [x] Real-time exchange rate lookup — reuses the existing `fx_adapters` (`mock` + Open Exchange Rates) `get_rate`; no new provider
- [x] Auto-convert to reporting currency — `services/currency_conversion.py` + per-org `Organization.settings.reporting_currency` (falls back to `payments.home_currency` → `invoice_defaults.currency` → `AP_REPORTING_CURRENCY_DEFAULT`); `/analytics/cfo` (`reporting_spend`) and `/dashboard` (`reporting` block) roll multi-currency invoices into one reporting currency. The rate is locked + materialized on the invoice (`reporting_amount` / `reporting_fx_rate` / `reporting_fx_locked_at`, migration 0025) — no silent recompute at today's rate
- [x] Realized/unrealized gain/loss tracking — payment-level realized (`compute_fx_gain_loss`, pre-existing) + open-position `compute_unrealized_fx_gain_loss` surfaced as `unrealized_fx` on `/analytics/cfo`
- [x] Currency displayed correctly per locale — frontend `<Money>` component + `formatMoney()` (`Intl.NumberFormat`, ISO-4217-code-driven) applied across invoices / payments / dashboard / analytics / portal; each amount renders with its own currency code, never a hardcoded `$`

### Multi-Entity
**Status:** Partial (multi-tenant exists, not multi-entity within org)

- [ ] Multiple entities (subsidiaries) within one organization
- [ ] Entity-level chart of accounts, GL codes, cost centers
- [ ] Inter-company invoice routing
- [ ] Consolidated reporting across entities

### Tax Compliance
**Status:** Done (US 1099 + international VAT/GST/withholding) — e-invoicing (Peppol/ZUGFeRD etc.) tracked separately under Priority 10. See `backend/docs/tax-1099.md` + `backend/docs/international-tax.md`.

1099 compliance is required for US AP operations. Bill.com, Tipalti, AvidXchange, Stampli, MineralTree all have it. VAT/e-invoicing is required for EU expansion (Medius and Basware lead).

**US Tax (Priority):**
- [x] W-9 collection — request, store, validate vendor W-9 forms (`POST /api/tax/vendors/{id}/w9`, vendor tax fields)
- [x] TIN validation — pluggable `tin_validation_adapters/` (offline `mock` default + Tax1099 TIN-match skeleton, local-first); `POST /api/tax/vendors/{id}/tin-verify` stamps `tin_verified_at`; format + checksum validation, raw TIN never logged
- [x] 1099 tracking — `build_1099_report` / `build_1099_dashboard` flag vendors over the $600 annual threshold
- [x] 1099-NEC and 1099-MISC generation — `services/tax_1099_forms.py` renders reportlab PDFs from payment data (`GET /api/tax/vendors/{id}/1099`); TIN masked in the PDF text layer
- [x] 1099 e-filing — pluggable `tax_filing_adapters/` (offline `mock` default + Tax1099 partner skeleton); `POST /api/tax/1099/file`, idempotent at two layers (DB unique constraint on `(org, idempotency_key)` in migration 0026 + deterministic adapter)
- [x] 1099 vendor dashboard — `GET /api/tax/1099-dashboard` (eligible vendors, YTD totals, W-9-on-file + TIN-verified status, threshold flags) + frontend `/tax` route

**International Tax:**
- [x] Tax rate lookup by jurisdiction — pluggable `tax_rate_adapters/` (offline `mock` default + Avalara / TaxJar skeletons, local-first), per-org override via `Organization.settings.tax.rate_provider`
- [x] VAT handling for international invoices — incl. EU reverse-charge (`services/international_tax/vat.py`)
- [x] Withholding tax calculation — by jurisdiction / vendor (`services/international_tax/withholding.py`)
- [x] GST handling (Australia, India, Canada) — `services/international_tax/gst.py`
- [x] Tax report generation — per-period collected-vs-owed report (`services/international_tax/report.py`, `/api/international-tax` router); figures persisted on `intl_tax_records` (migration 0027) as the audit fact
- [x] Country-specific tax rules engine — data-driven `services/international_tax/country_rules.py`; new countries are config, not code

**Competitors:** Tipalti (1099 + W-8BEN + VAT), Bill.com (1099 e-filing), Basware (global VAT, 60+ countries), Medius (EU e-invoicing mandates)

### Multi-Language UI (Internationalization / i18n)
**Status:** Planned

The data layer is already internationalized (multi-currency rollups, locale-aware `Intl` money/date formatting, country tax rules, e-invoicing) — but every label, button, email, and error string is still hardcoded English. Localizing the **presentation** layer is the remaining piece for genuine international reach (EU mandates, LATAM, APAC, MENA). Basware/Medius ship 20+ UI languages; Tipalti and Bill.com localize the supplier-facing surfaces. Starter set: `en, de, fr, es, pt-BR, ja` (the six [`../project-running`](../../project-running) already ships), with the RTL switch-point in place for a later `ar`/`he`.

**Web (SvelteKit, `frontend/`):**
- [ ] i18n runtime under `frontend/src/lib/i18n/` — client-side locale detection on first mount (stored choice → `navigator.languages` → English), reactive `m(key, params)` lookup, `<html lang/dir>` applied. **No `Accept-Language` SSR hook** — the frontend is adapter-static (GitHub Pages), so detection must be client-side
- [ ] English statically bundled (fallback dict + prerender default); every other locale a dynamic `import()` chunk via a typed loader registry, so a single-locale visitor downloads only their strings — i18n adds ~nothing to the initial payload
- [ ] Compile-time + runtime parity: `Messages = typeof en` + `satisfies Messages` per locale (missing/extra key = type error); a `messages_parity` vitest validating every locale is loadable, complete, non-empty, and placeholder-faithful
- [ ] ICU inline plurals (`{n, plural, one {…} other {…}}`) resolved via `Intl.PluralRules` for the active locale — not `fooOne`/`fooOther` key pairs (keeps web and mobile plural shapes identical)
- [ ] Locale picker in settings/shell (endonyms — each language in its own script), choice persisted to `localStorage`
- [ ] Active locale drives the existing `Intl.NumberFormat`/`Intl.DateTimeFormat` formatters (`<Money>` / `formatMoney()` / date helpers) so numbers, currency, and dates localize together
- [ ] RTL switch-point (`dirForLocale`) wired to `<html dir>`; audit CSS for logical properties so an `ar`/`he` catalogue drops in with no further layout plumbing
- [ ] Incremental string extraction — shell/nav first, then route-by-route; an un-extracted literal simply stays English until its turn

**Mobile (Flutter, `mobile/`):**
- [ ] Standard Flutter `gen-l10n` + `intl` + `.arb` catalogues (idiomatic path — plural/placeholder/ICU + `Localizations.localeOf`), committed (non-synthetic) output, same six locales
- [ ] Per-device locale via the existing prefs store → `localeNotifier` → `MaterialApp.locale` (language is a device choice, like theme/units — **not** account-roamed)
- [ ] ARB key-parity test mirroring the web `messages_parity`

**Server-side (FastAPI, `backend/`):**
- [ ] Localized outbound email — emails render server-side, so the recipient's language must reach the backend. Persist a DB-synced `locale` preference (on `User` + `VendorUser`), written as a side effect of the UI language picker, consumed by a per-locale email catalogue with English fallback. Covers the `email_adapters` surfaces: signup/welcome, notifications, supplier-chat + portal-link emails
- [ ] Email catalogue parity test (every locale has every key, no empty strings); deep links + brand chrome stay locale-independent — only copy changes
- [ ] Keep the DB `locale` pref **separate** from the per-device UI locale — it means "what language to email this person in" (account-level), never read back to drive in-app UI

**Pointers from `../project-running`** (it shipped exactly this — three translation surfaces kept in lockstep by parity tests, no shared source because TS/Dart/Python can't import one catalogue):
- Web runtime to model on: `apps/web/src/lib/i18n/` — `locale.ts` (pure negotiation: `SUPPORTED_LOCALES`, `negotiateLocale`, `dirForLocale`, `parseAcceptLanguage`), `messages.ts` (`Messages = typeof en`), `catalogues.ts` (typed lazy-loader registry), `store.svelte.ts` (reactive `m()` + `setLocale` + `initLocale`), `interpolate.ts` (ICU plural + `{placeholder}` substitution), `messages_parity.test.ts`, and `locales/*.ts`
- Decision records spelling out the *why* and the traps to avoid: `docs/architecture/decisions.md` §108 (web client-side + lazy catalogue), §113 (mobile gen-l10n/ARB + per-device locale), §120 (server-side email localization from a DB-synced pref — the one place locale leaves the device)
- Reuse the design wholesale; the only AP-specific delta is that **two** identities email-localize (internal `User` and supplier-portal `VendorUser`) and the email catalogue lives in Python (`backend/app/services/email_adapters/`), not Go

**Competitors:** Basware / Medius (20+ UI languages, EU-mandate-driven), Tipalti & Bill.com (localized supplier portals), SAP Ariba / Coupa (full enterprise localization)

---

## Priority 6: Supplier Portal
**Competitive gap: all competitors have a supplier portal**

### Vendor Self-Service
**Status:** Partial — Phase 2 self-service shipped (PO flip, remittance download, approval-gated company/bank/tax self-update) on top of the Phase 1 MVP (separate auth, invoice submission, status/payment tracking). See [`backend/docs/supplier-portal.md`](../backend/docs/supplier-portal.md).

Separate portal for vendors to interact with the AP system. Biggest workflow gap — forces email/manual invoice intake without this. Every competitor (Coupa CSP, Tipalti Supplier Hub, Basware Network, Stampli) offers this.

- [x] Vendor login (separate auth, linked to vendor record) — `VendorUser` tenant-scoped, JWT `typ=vendor` prevents cross-contamination with AP-app tokens
- [x] Submit invoices directly (upload PDF) — routes into the existing extraction pipeline with `vendor_id` pre-filled and a `source=supplier_portal` audit breadcrumb
- [x] Check invoice status and payment status
- [x] View payment history — joins `payments` ↔ `invoices` on `vendor_id`
- [x] Admin invite flow — `POST /api/vendors/{id}/portal-users` mints a temp password + welcome email
- [x] PO flip — create invoice from PO (pre-populate fields) — `POST /api/portal/purchase-orders/{id}/flip` seeds an invoice from a vendor-owned PO into the existing extraction/workflow path; idempotent per `(vendor, po)`
- [x] Download remittances (PDF generation) — `GET /api/portal/payments/{id}/remittance` reuses `services/remittance_pdf.py`, ownership-joined on `Invoice.vendor_id`
- [x] Update company info, bank details, tax ID — `GET/PATCH /api/portal/company` (contact fields apply live, masked bank/tax) + `POST /api/portal/company/{bank-change,tax-id-change}` staging
- [x] Bank detail change requires AP admin approval (fraud prevention) — bank/tax changes stage a `VendorChangeRequest`; the vendor row is untouched until an admin approves via `POST /api/vendors/change-requests/{id}/approve`
- [ ] W-9/W-8 form upload and management
- [ ] Notification preferences (email on payment, on rejection)
- [ ] Virtual card detail viewing (secure, one-time access)
- [ ] Early payment discount offers (tie into dynamic discounting)
- [ ] In-app per-invoice chat between vendor and AP team
- [ ] MFA for portal users

**Files:** `backend/app/api/portal.py`, `backend/app/api/portal_auth.py`, `backend/app/models/vendor_user.py`, `frontend/src/routes/portal/`

**Competitors:** Coupa (CSP, free for suppliers), Tipalti (full supplier hub), Basware (Basware Network), Stampli (invoice submission + status)

---

## Priority 7: Authentication & Enterprise Security
**Competitive gap: SSO is an enterprise deal-blocker**

### SSO / Enterprise Authentication
**Status:** OIDC + SAML + SCIM (/Users + /Groups) shipped

No SSO = no enterprise sale. OIDC (Okta + Entra), SAML 2.0, and SCIM 2.0 user provisioning are live. See [`docs/authentication.md`](authentication.md) § SSO and § SCIM for the full design, and [`docs/local-sso-saml.md`](local-sso-saml.md) for local SAML testing via Keycloak.

- [x] OIDC (OpenID Connect) support — single flow covers Okta + Entra via per-tenant discovery URL
- [x] JIT (Just-In-Time) user provisioning from SSO — match by `(provider, sub)` then `(org, email)`, otherwise create
- [x] SCIM 2.0 `/Users` provisioning (create / list / get / PATCH / soft-delete) with per-tenant bearer token
- [x] Force password change on first login (non-SSO users) — `User.must_change_password` flag, cleared on `/api/auth/change-password`
- [x] SAML 2.0 SSO (Okta, Azure AD, OneLogin, ADFS) — SP-initiated, separate code path (`api/auth_saml.py`) reusing the OIDC JIT + session-mint tail. python3-saml verification pinned to the per-tenant IdP cert; hardened (wantAssertionsSigned, SHA-256-only, issuer/audience/destination/InResponseTo enforced, per-tenant replay dedup, XXE-hardened parsing). Local IdP via Keycloak (`pnpm saml:seed`).
- [x] SCIM `/Groups` — IdP groups → RBAC roles. Group state JSONB on `settings.sso.scim_groups`; `scim_group_role_map` (`{displayName: role}`) drives idempotent role reconciliation (only mapped roles are added/removed; manual/JIT assignments untouched). Full list/get/create/PUT/PATCH/delete. `services/scim_groups.py`
- [x] SSO-only mode — `settings.sso.sso_only` (covers OIDC + SAML) closes password login org-wide: `/api/auth/login` 403s with an `sso_only` audit reason, and the login page hides the password form. `sso_only` is echoed on the public `/config` endpoints only when the IdP config resolves, so a broken config can't lock everyone out. `services.sso.is_sso_only`
- [x] MFA — TOTP enrollment + email-OTP backup, opt-in per user with org-level enforcement toggle (`AP_MFA_ENABLED` master switch; default off in dev)
- [ ] MFA — WebAuthn / passkeys (TOTP shipped first; passkeys are a separate code path)
- [ ] MFA — mobile app support (Flutter login currently expects `TokenResponse` only)
- [x] Session management — per-user concurrent session cap + forced logout on role change / deactivation (see SOC 2 Readiness below)

**Competitors:** All competitors support SSO. Coupa, SAP Concur, and Basware also support SCIM.

---

### SOC 2 Readiness
**Status:** Engineering prereqs complete — **all code controls landed; process work pending founder sign-off**

SOC 2 Type I (design) → Type II (operating over time) is the table-stakes security attestation for selling to finance teams. Full plan in [`docs/soc2-readiness.md`](soc2-readiness.md) — vendor comparison, control mapping, timeline, and what the founder still needs to do as a human.

**Engineering prerequisites:**
- [x] Access reviews — `backend/scripts/access_review.py` exports every user × role × org as CSV (quarterly)
- [x] Backup + DR runbook — `docs/backup-disaster-recovery.md` with RTO/RPO + restore procedures
- [x] Secrets rotation runbook — `docs/secrets-rotation.md` (cadence + procedure for every secret)
- [x] Vulnerability scanning in CI — Dependabot (shipped) + CodeQL SAST (Python + JS) + Trivy on the backend container, weekly + on push (`.github/workflows/security.yml`)
- [x] RBAC enforcement at API layer (separate roadmap item — already done)
- [x] MFA support + org-level enforcement (separate roadmap item — already done)
- [x] Session management — per-user concurrent session cap (Redis sorted set, `AP_MAX_CONCURRENT_SESSIONS`), forced logout on role change / deactivation (`services.session_management.revoke_user_sessions`)
- [x] Centralized audit log shipping — background shipper loop + adapters (CloudWatch Logs + S3 Object Lock) at `backend/app/services/audit_log_shipper.py` + `services/audit_shipping/`. See `backend/docs/audit-log-shipping.md`.
- [x] Auth event audit log — login/logout/MFA/SSO events written via `app/services/audit_dispatch.py::dispatch_auth_audit` into the tenant `audit_log` table
- [x] HSTS header + security-header middleware (`backend/app/main.py` `SecurityHeadersMiddleware`, gated on `AP_HSTS_ENABLED`); TLS smoke script at `backend/scripts/verify_tls.py`
- [x] KMS key auto-rotation flag in Terraform — `infra/kms.tf` `enable_key_rotation = true`
- [x] S3 versioning + Object Lock in Terraform — `infra/s3.tf` (versioning Enabled; invoice-files GOVERNANCE 365d, audit-logs COMPLIANCE 2555d)

**Process / attestation work** (founder, not engineer):
- [ ] Vendor selection — Vanta, Drata, Secureframe, or Sprinto. See `docs/soc2-readiness.md` § Vendor comparison.
- [ ] Policy library — info security, incident response, change management, access control, vendor mgmt (vendor templates)
- [ ] Employee onboarding / offboarding checklist with evidence collection
- [ ] Incident response runbook + on-call rotation
- [ ] SOC 2 Type I audit (point-in-time) — typical 4–8 weeks after prereqs
- [ ] Begin Type II observation window (6+ months) for annual renewal

**Competitors:** Every serious competitor has SOC 2 Type II. Without it, enterprise deals stall at security review.

---

## Priority 8: Mobile & Notifications
**Competitive gap: most competitors have mobile apps**

### Flutter Mobile App
**Status:** Partial — boilerplate built (iOS + Android), core screens working

Flutter app at `mobile/` with login, dashboard, invoice list, approve/reject, payments, settings. Same backend API as web.

**Done:**
- [x] Login with tenant selection
- [x] Dashboard (KPIs, aging buckets, top vendors)
- [x] Invoice list with search + status filter chips
- [x] Invoice detail with approve/reject
- [x] Approvals tab with swipe-to-approve
- [x] Payment history list
- [x] Role-based bottom navigation
- [x] Settings (profile, tenant info, logout)
- [x] JWT in secure storage (iOS Keychain / Android Keystore)
- [x] API contract tests in backend to prevent client breakage

- [x] Camera OCR — snap photo or pick from gallery → upload → trigger AI extraction
- [x] Push notifications — Firebase Cloud Messaging + local notifications (no-op until Firebase configured)
- [x] Offline mode — SQLite cache for dashboard and invoice list, serves cached data on network failure
- [x] Biometric login — Face ID / fingerprint / device PIN toggle in settings, checked on app launch

**Medium priority — parity with web (see `mobile/CLAUDE.md` for full gap list):**
- [ ] Invoice upload via file picker (PDF/PNG/JPG/TIFF support)
- [ ] Invoice editing (change fields in detail screen)
- [ ] Activity timeline in invoice detail (audit log)
- [ ] PDF/image viewer for uploaded invoice files
- [ ] Exception queue (list, resolve, escalate, dismiss)
- [ ] Vendor management (list, verify/reject, ERP sync)
- [ ] Payment queue (select invoices, choose method)
- [ ] Payment runs (create/execute batches)
- [ ] Payment summary cards (total paid, pending, rebates)
- [ ] Advanced search modal (vendor, PO, amount range, date range)
- [ ] Invoice warnings/fraud flags display
- [ ] ERP status display on invoice detail

**Low priority — admin features (less needed on mobile):**
- [ ] Bulk operations (select multiple, delete, status change)
- [ ] Export (CSV/XML)
- [ ] Workflow management
- [ ] Organization settings
- [ ] Admin user management

**Files:** `mobile/` — see `mobile/CLAUDE.md` for full structure

### Email & Notification System
**Status:** In progress

- [x] Email notifications on key events (invoice assigned, approved, rejected, paid) — centralized `transition_invoice` hook + explicit `assign_reviewer` hook → `notification_dispatch.notify_event`, sent via the existing pluggable email adapter (`console`/`smtp`/`ses`). See `backend/docs/notifications.md`.
- [x] Configurable notification preferences per user — `users.notification_prefs` JSONB, per-event in-app/email toggles in `/profile`, gating both channels.
- [x] In-app notification center — tenant `notifications` table, `/api/notifications*`, `/notifications` route + sidebar unread badge.
- [ ] Email-to-invoice — forward invoices to a dedicated email address for auto-import (Bill.com, Tipalti, Stampli, Medius have this)
- [ ] Slack/Teams integration for approval notifications (Stampli, Airbase differentiate here)
- [ ] Mobile parity — the email/in-app backend serves mobile for free once a `NotificationsScreen` calls `GET /api/notifications`; no mobile screen ships in this slice.

---

## Priority 9: AI-Powered Automation (strong differentiators)

### AI Agents for Autonomous Exception Handling
**Status:** In progress (first slice: amount-mismatch resolver shipped)

AI agents that autonomously resolve common exceptions without human intervention — mismatched amounts, missing PO references, GL coding errors. See `backend/docs/exception-agents.md`.

- [x] Agent framework — registry + coordinator + autonomy thresholds (`services/exception_agents/`)
- [x] Auto-resolve: small amount mismatches within tolerance (`amount_mismatch_v1`)
- [ ] Auto-resolve: missing PO — match by vendor + amount + date range *(deferred — stub escalates)*
- [ ] Auto-resolve: GL coding errors — correct based on historical patterns *(deferred)*
- [x] Escalation rules — sub-threshold confidence routes to human (`escalated`)
- [x] Agent decision log — `AgentDecision` table + `/api/exceptions/agent-decisions`
- [ ] Dashboard: agent resolution rate, accuracy, escalation rate *(API delivered: `/agent-stats`; UI deferred; accuracy is a placeholder pending a human-overturn signal)*
- [x] Configurable autonomy level per org (conservative → aggressive)

---

### Adaptive AI Workflows
**Status:** First slice shipped (read model + anomaly read + advisory suggestions)

Workflows that learn from team behavior and adapt over time — routing, approval timing, exception handling. The first slice ships the **read** surfaces (learning, on-demand anomaly, advisory suggestions); the **act** surfaces (smart routing, auto-adjust thresholds, A/B, retraining) remain follow-ups. All learning + anomaly detection is deterministic statistics over existing tenant data — no LLM, runs with no cloud key.

- [x] Adaptive approval-pattern learning (read model) — per-approver + per-vendor approval stats (counts, approval/consistency rates, time-to-approve). `services/adaptive_workflows.py`, `GET /api/adaptive/approval-patterns`.
- [x] Baseline anomaly detection (on-demand, explainable) — `GET /api/adaptive/anomalies`; flags amount / approver / timing deviation and **returns the per-vendor baseline it compared against**. Read-only — distinct from (and does not duplicate) the per-invoice `fraud_stat_anomaly` warning, which writes warnings + Exceptions.
- [x] Advisory workflow-change suggestions — "consider auto-approve under $X" auto-approve-threshold suggestions persisted in `workflow_suggestions` (migration 0031) with `open/dismissed/applied/stale`; advisory only — nothing is auto-applied.
- [ ] Smart routing — assign invoices to the fastest/most-appropriate approver *(follow-up)*
- [ ] Auto-adjust thresholds — raise auto-approve limit as accuracy improves *(follow-up; the apply path must route through the audited `review.approve_invoice` / workflow-definition PATCH)*
- [ ] A/B testing for workflow rules — compare performance of different configs *(follow-up)*
- [ ] Feedback loop — corrections feed back into the AI model *(follow-up)*

**Files:** `backend/app/services/adaptive_workflows.py`, `backend/app/api/adaptive_workflows.py`, `backend/app/schemas/adaptive_workflows.py`, `backend/app/models/adaptive_suggestion.py`, `backend/alembic/versions/0031_workflow_suggestions.py`, `backend/docs/adaptive-workflows.md`, `backend/tests/test_adaptive_workflows.py`

---

### Intelligent Data Enrichment from Supplier History
**Status:** Planned

Auto-populate and validate invoice fields using historical data from the same supplier.

- [x] Auto-fill GL account, cost center, payment terms from vendor history — GL/cost-center/terms suggested from the vendor's approved-invoice history (dominant value + confidence + evidence); suggestion-only, never overwrites. `GET /api/enrichment/invoices/{id}/suggestions`. See backend/docs/data-enrichment.md.
- [ ] Flag deviations — "This vendor usually invoices ~$5K, this one is $50K" — already shipped via `adaptive_workflows.detect_invoice_anomaly` (`GET /api/adaptive/anomalies`); not duplicated here.
- [ ] Vendor performance scoring — on-time delivery, invoice accuracy, dispute rate — accuracy + dispute sub-scores shipped (`GET /api/enrichment/vendors/{id}/score`); on-time delivery deferred pending a PO expected-date column.
- [ ] Suggest vendor consolidation — identify duplicate/similar vendors
- [ ] Enrich vendor data from external sources (D&B, Clearbit)
- [x] Price variance detection — same item, different price across invoices — per-vendor line-item median baseline + tolerance; returned inline on the suggestions endpoint with baseline+delta. Persisting as a warning/exception is a tracked follow-up.

---

### Conversational AP Assistant
**Status:** First slice shipped — **Differentiator for CFO / AP Manager persona**

Chat over the tenant's data. Replaces ad-hoc SQL and spreadsheet exports for common operational questions. Backend `/api/assistant/*`; see `backend/docs/conversational-assistant.md`.

- [x] Tool-calling assistant with a fixed toolset: `list_invoices(filters)`, `get_vendor_spend(period)`, `list_pending_approvals(assignee)`, `get_payment_forecast(horizon)`, `find_invoices_by_text(query)` — no raw SQL exposure, each tool is a typed endpoint over the current tenant. Local-first: deterministic `mock` adapter default, `claude` adapter (Anthropic tool-use) when keyed.
- [x] Tenant-scoped context — conversation history per `(tenant, user)`, org-level cap on tokens/cost.
- [ ] Streaming responses via server-sent events; charts rendered from structured tool output. *(API already returns chartable structured `result`; SSE + chart UI deferred.)*
- [ ] Example prompts built into the empty state: *"which approvals have I been sitting on > 5 days?"*, *"which vendors are we paying the most this quarter?"*, *"show me invoices with PO mismatches over $10k"*. *(frontend, deferred.)*
- [x] Cost controls — token budget per org per month with a usage meter (`/api/assistant/usage`); graceful 429 refusal on exceed. *(UI surfacing deferred.)*
- [x] Audit trail — every tool call logs a PII-safe `assistant.tool_invoked` row via the append-only audit infra.

Highest-leverage "sticky feature" work once the product has real usage. Cold-start is fine because it's retrieval over existing data, not learned patterns.

---

### Audit Log Summarization
**Status:** Done

One-paragraph natural-language summary at the top of the invoice modal, generated from the audit log + extraction metadata. Dramatically improves the "catching up on an invoice" UX — reviewers don't have to parse a 15-row timeline.

- [x] Cached summary field on `invoices.meta` (regenerate when the audit-log fingerprint changes — derived from `audit_log`, so it works in both `local` and `lambda` audit modes with no audit-write-path changes)
- [x] LLM call invoked lazily on first open after audit log changes (`services/audit_summary.py`, `GET /api/invoices/{id}/summary`)
- [x] Handles all status transitions, corrections, exception resolutions, ERP sync events
- [x] Shows confidence context: *"auto-extracted at 95% confidence with RAG priors applied"*
- [x] Small feature but high-visibility — pairs well with the invoice-list priors chips

Local-first: with no Anthropic key (committed `.env.development` default) the service returns a deterministic template summary — no network call. See `backend/docs/audit-summary.md`. Mobile is excluded (the audit timeline it builds on is not yet on mobile — Priority 8 parity item).

---

### Predictive Cash Flow Forecasting
**Status:** Done — GET /api/analytics/cashflow_forecast (+ /cashflow_whatif, /cash_position) over Invoice/PaymentSchedule/Payment; what-if early/on-time/late with discount capture; cash-position with BYO opening balance + threshold alerts; CSV export via the analytics/export registry; CFO web dashboard at /cfo. Bank-balance auto-sync + persisted thresholds + mobile deferred.

Use AP data to forecast cash outflows and optimize payment timing.

- [x] Forecast daily/weekly/monthly cash outflows from pending invoices
- [x] Factor in payment terms, early-pay discounts, and approval pipeline
- [x] "What-if" scenarios — impact of paying early vs. on-time vs. late
- [x] Cash position dashboard with AP commitments overlay
- [x] Alert when projected outflows exceed thresholds
- [~] Integration with bank balance data for complete cash picture — bring-your-own opening balance (query param or `Organization.settings.cashflow.opening_balance`); a live banking-feed auto-sync is deferred
- [x] Export forecasts for CFO reporting (CSV via `/api/analytics/export/cashflow_forecast`)

---

## Priority 10: Compliance & E-Invoicing

### Sanctions & Vendor Risk Screening
**Status:** Shipped (first slice) — screening on vendor create/update, periodic re-screen sweep, payment-block gate, adverse-media support, composite risk scoring, Dow Jones/Refinitiv/ComplyAdvantage adapter skeletons, and the append-only screening trail all landed. Real-provider wiring (live keys) + a dedicated review-queue page are the remaining deployment work.

Tipalti, Coupa, Medius, and Basware all screen vendors against sanctions lists. Required for financial services, government contractors, and regulated industries. See `backend/docs/vendor-risk-screening.md`.

- [x] OFAC/SDN sanctions screening on vendor creation and update — `services/vendor_screening.screen_vendor_record` runs on `POST`/`PATCH /api/vendors` (best-effort, savepoint-isolated so a provider failure never blocks the vendor write) + manual `POST /api/vendors/{id}/screen`. Gated by `AP_VENDOR_SCREENING_ENABLED` (default on; mock-safe local-first).
- [x] Ongoing monitoring — re-screen vendors periodically (daily/weekly) — `services/vendor_rescreen.py` background sweep (mirrors `contract_renewal`): re-screens active vendors whose `last_screened_at` is stale per `AP_VENDOR_RESCREEN_AFTER_DAYS`. Disabled by default (`AP_VENDOR_RESCREEN_ENABLED`).
- [x] Flag and block payments to sanctioned entities — a `match` sets `vendors.payments_blocked`; `check_payment_compliance` refuses a blocked vendor up front (before FX lock / any adapter call). Manual `POST /api/vendors/{id}/block` \| `/unblock`.
- [x] Adverse media screening — `ScreeningResult.categories` (`("adverse_media",)`); mock fixtures + provider adapters surface adverse-media hits via the same path (list NAME `ADVERSE_MEDIA`).
- [x] Vendor risk scoring (sanctions + fraud signals + payment history) — `services/vendor_risk_scoring.py` blends latest sanctions check + open `fraud_flag` exceptions + trailing-12m payment history into a 0–100 composite + bucket (PII-free factors). `GET /api/vendors/{id}/risk`, `POST .../risk/recompute`, `GET /api/vendors/risk/summary`.
- [x] Integration with screening providers (Dow Jones, Refinitiv, ComplyAdvantage) — `sanctions_adapters/`: `mock` (default), `complyadvantage`, `dowjones`, `refinitiv` (skeletons — live key required, fail-closed without one). Selected per-org via `Organization.settings.compliance.sanctions.provider`.
- [x] Screening audit trail — log all checks and results — append-only `sanctions_checks` (every screen: initial/periodic/manual/pre_payment) + PII-free `vendor.screened` audit rows; `GET /api/vendors/{id}/screening-history` + `GET /api/vendors/screening/review-queue`.

**Competitors:** Tipalti (OFAC/SDN built-in), Coupa (community risk), Basware (sanctions + fraud module), Medius (fraud intelligence)

---

### SOX-Compliant Audit Trails
**Status:** Partial — immutable log + access auditing + field history + auditor export shipped; periodic access reviews / retention / digital signatures deferred (each its own slice, tracked below)

Enhance the existing audit trail to meet SOX (Sarbanes-Oxley) compliance requirements.

- [x] Immutable audit log — DB-level `BEFORE` triggers on `audit_log` reject every DELETE and every UPDATE that touches a column other than `shipped_at` (the shipper's carve-out). Survives a rogue ORM call or direct `psql`. See `app/services/audit_immutability.py` + migration `0022_sox_audit_immutable`; installed on every tenant DB (migration fan-out + `tenant_provisioning`).
- [x] Segregation of duties enforcement — default-on in the approval step; see `app/services/approval_chain.py::check_segregation`
- [x] Access control audit — log who viewed what, not just who changed what. Sensitive reads (vendor detail, payment detail, card PAN, audit-trail view, every export) write a `<entity>.viewed` row via `app/services/audit_access.py::log_access`, recording field-NAMES not values (PII-out-of-logs).
- [ ] Periodic access reviews — flag users with unused elevated permissions *(deferred — own slice; trigger: SOC 2 Type II access-review control. Needs a per-user last-elevated-use index + a review workflow.)*
- [ ] Retention policies — configurable retention periods, archival *(deferred — own slice; trigger: customer retention SLA. Must compose with the immutability trigger, i.e. archival via a privileged, audited path, not a raw DELETE.)*
- [ ] Audit report generation — formatted for external auditors *(partially covered by the export below; formatted PDF report deferred to its own slice.)*
- [ ] Digital signatures on approvals (timestamp + user hash) *(deferred — own slice; trigger: auditor request for cryptographic non-repudiation. Needs a signing-key story + verification endpoint.)*
- [x] Change history on every field — before/after values. Invoice edits + approve-with-corrections write `details.changes = {field: {old, new}}` (money serialised as string-Decimal, never float) via `audit_access.build_field_diff`; rendered in the invoice-modal Activity timeline.
- [x] Export audit trail per invoice or date range for auditor review — `GET /api/audit/export` (JSON/CSV, admin/CFO) + the `/audit` auditor console. Every export is itself audited (`audit.exported`).

---

### Automated E-Invoicing
**Status:** Inbound + outbound UBL 2.1 shipped (parse + generate, auto-detect, schema validation, country VAT/GST/IVA tax validation); PEPPOL AS4 **outbound send AND inbound receive** shipped (hosted Access Point adapter, mock default, idempotent transmission log; inbound webhook dedupes redeliveries by AS4 MessageId and routes to the einvoice extractor). Country-specific outbound formats — **FatturaPA (IT), CFDI 4.0 (MX), NF-e (BR), DIAN (CO)** — shipped as pure local-first generators + national validation, wired into the export route via `?format=` and registered behind `e_invoice/country_formats/`; **live government clearance** (SdI / SAT-PAC / SEFAZ / DIAN authorization) is the one remaining deferral, tracked per-format below.

Support structured electronic invoice formats required in the EU, Australia, and other regions. Inbound parsing is pure/local-first (no network, no SaaS key) and on by default; see `backend/docs/e-invoicing.md`.

- [x] Factur-X / ZUGFeRD — hybrid PDF/XML format (EU standard): embedded CII XML extracted from PDF/A-3 and parsed
- [x] UBL (Universal Business Language) 2.1 — **parse + generate** (PEPPOL BIS Billing 3.0 payload). `generate_ubl(doc) -> bytes` is the exact inverse of the parser; round-trip property `parse_ubl(generate_ubl(doc)) == doc` holds on core fields
- [x] Auto-detect format on upload — structured data parsed instead of OCR (`extraction.run_extraction` choke point routes to the `einvoice` adapter at confidence 1.0)
- [x] Validate against schema — malformed e-invoices rejected with clear field-level errors (EN 16931 structural subset)
- [x] UBL 2.1 **generate** (outbound) — reuses `EInvoiceDocument` via `mapper.invoice_to_einvoice_document`; `GET /api/invoices/{id}/einvoice` (role-gated AP export, 422 on tax-invalid) + `GET /portal/invoices/{id}/einvoice` (vendor-scoped supplier download). CII generate deferred (own slice; trigger: a corridor that requires CII outbound)
- [x] Peppol BIS Billing 3.0 — **receive and send** via Peppol network shipped. **Send:** `POST /api/invoices/{id}/peppol-send` (reuses the UBL generator; `PEPPOL_BIS_BILLING_DOCTYPE`/`PROCESSID` constants). **Receive:** `POST /api/peppol/inbound/{tenant_slug}` (public-by-design, HMAC-gated webhook; dedupes redeliveries by AS4 MessageId via the `uq_peppol_message_id` index; parses the inbound UBL with the existing `e_invoice` parser, creates the Invoice, and hands to `dispatch_extraction` → the `einvoice` adapter). Reuses the `PeppolTransmission.direction`/`message_id` columns, `ParticipantId`, and `webhook_security`
- [x] FatturaPA — Italian e-invoicing format. `e_invoice/country_formats/fatturapa.py` generates the `FatturaElettronica` v1.2 (`FPR12`) document (DatiTrasmissione + Cedente/Cessionario header, DatiGeneraliDocumento/DatiBeniServizi/DatiRiepilogo body) + national validation (seller **and** buyer Partita IVA required). *Deferred: SdI transmission + the `.p7m` (CAdES) digital signature — own slice; trigger: first IT customer going live.*
- [x] CFDI 4.0 — Mexican e-invoicing. `country_formats/cfdi.py` generates `cfdi:Comprobante` v4.0 (Emisor/Receptor RFC, Conceptos, Impuestos) + national validation (emisor **and** receptor RFC required). *Deferred: SAT-PAC stamping → `Sello`/`Certificado`/`tfd:TimbreFiscalDigital` UUID (folio fiscal) — own slice; trigger: first MX customer going live.*
- [x] NF-e / NFS-e — Brazilian electronic invoicing. `country_formats/nfe.py` generates `NFe/infNFe` v4.00 (ide/emit/dest/det·prod/total·ICMSTot) + national validation (emit CNPJ required). *Deferred: SEFAZ authorization → 44-digit chave de acesso + protocolo + digital signature (a deterministic placeholder `Id` is emitted meanwhile); municipal NFS-e schema — own slice; trigger: first BR customer going live.*
- [x] DIAN — Colombian e-invoicing. `country_formats/dian.py` generates DIAN-profiled UBL 2.1 (`CustomizationID=10`, DIAN `ProfileID`, `UBLExtensions` placeholder) + national validation (supplier NIT required). *Deferred: CUFE + XAdES signature + `dian:DianExtensions` injected at clearance — own slice; trigger: first CO customer going live.*
- [x] Access point / PEPPOL AS4 gateway integration — **send and receive** shipped (`services/peppol_adapters/`: mock in-process default + `as4_gateway` real adapter talking to a hosted AP's HTTP API; SMP/SML resolution behind `resolve_participant`; SBDH wrapping in the adapter, not the generator). Inbound delivery: both adapters implement `parse_inbound`; the AP's inbound POST is verified (`AP_PEPPOL_INBOUND_SIGNING_SECRET`) and deduped at the receive webhook. See `backend/docs/peppol.md`
- [x] Country-specific tax validation (VAT, GST, IVA) — `e_invoice/tax_rules.py`: per-country tax-ID format (EU/GB VAT, AU ABN, NZ/IN/CA GST, MX/ES/IT IVA), rate plausibility per regime, zero-rate/reverse-charge handling. Pure, PII-free `FieldError`s; wired into inbound `validate_document` + the outbound export guard

---

### Data Privacy & Residency (GDPR / CCPA)
**Status:** Planned

Selling internationally means handling vendor + employee PII and banking data under GDPR (EU/UK), CCPA/CPRA (California), and similar regimes. The app stores this across tenant DBs today but has **no** data-subject-request path, retention policy, or residency story — a hard blocker for EU/enterprise deals and a real legal exposure. Pairs with the [Multi-Language UI](#multi-language-ui-internationalization--i18n) work as the "go international" track.

- [ ] DSAR export — assemble everything held about a data subject (a `VendorUser`, contact, or `User`) into a portable bundle. New `/api/privacy` router, RBAC-gated (admin/DPO), the request itself audited
- [ ] Right-to-erasure / anonymization — delete or irreversibly anonymize a subject's PII while preserving the **immutable financial + audit record** (legally-required retention wins over erasure for transactional rows — redact PII fields, keep the money trail). Must respect the `audit_log` immutability triggers
- [ ] Configurable data-retention policies — per-tenant retention windows with a background purge sweep (mirror the `contract_renewal` loop pattern); document the legal-hold carve-out
- [ ] Data residency — pin a tenant's DB + object storage (MinIO/S3) to a region (`eu`, `us`, …); the database-per-tenant architecture already makes per-region placement tractable. Document the model even before multi-region infra ships
- [ ] Consent + processing records — cookie/consent banner on the marketing + portal surfaces, and a Record of Processing Activities (RoPA) doc; DPA template in `docs/founder-runbooks/`
- [ ] Sub-processor register + breach-notification runbook (72-hour GDPR clock) under `docs/`

**Competitors:** every EU-serving competitor (Basware, Medius, SAP Ariba, Coupa) has GDPR DSAR + residency; it's table stakes for enterprise procurement reviews

### Accessibility (WCAG 2.2 AA / EU EAA / ADA)
**Status:** Planned

Legally required, not optional: the **EU Accessibility Act** is in force (June 2025), and US ADA Title III + Section 508 apply to enterprise buyers. Components already carry some `aria-*` usage, but there's no systematic conformance target, audit, or regression guard. An `audit:accessibility` skill + `compliance-auditor` agent already exist to drive this.

- [ ] Adopt **WCAG 2.2 AA** as the conformance target across web (SvelteKit), mobile (Flutter), and the supplier portal; publish a VPAT/ACR
- [ ] Web baseline — keyboard navigability (no traps — there's a `ux-hunt` check for this), visible focus rings, semantic landmarks/roles, form-label + error association, `aria-live` on async/toast surfaces, AA contrast on `StatusBadge`/charts
- [ ] Audit-and-fix pass via the existing `audit:accessibility` skill, route by route (shared `lib/components/` first so fixes propagate); track findings to closure (no dangling deferrals)
- [ ] Automated regression guard — `axe-core` assertions wired into the Playwright e2e suite so a regression fails CI; mirror with Flutter's accessibility guidelines / `flutter test` semantics checks on mobile
- [ ] Screen-reader pass (VoiceOver / NVDA / TalkBack) on the core invoice → approve → pay flow and the supplier portal; respect `prefers-reduced-motion`

**Competitors:** enterprise suites (Coupa, SAP Ariba, Basware) ship VPATs; a clean ACR is increasingly a procurement gate, especially for public-sector + EU buyers

---

## Priority 11: Dynamic Payments & Matching

### Dynamic Discounting & Early Payment Optimization
**Status:** Shipped (first slice)

Go beyond static early-pay discounts — dynamically negotiate and optimize payment timing.
`DiscountOffer` model + migration 0043, `/api/discounts` router, the
`discount_roi` / `discount_offers` / `discount_optimizer` / `discount_auto_trigger`
services, the `financing_adapters` package, and the `/discounts` web dashboard.
See `backend/docs/dynamic-discounting.md`.

- [x] Supplier-offered dynamic discounts — "Pay in 5 days for 3% off" (sliding scale) — `DiscountOffer.tiers` JSONB sliding scale, `source=supplier`
- [x] AI-optimized payment timing — maximize discount capture vs. cash preservation — `discount_optimizer.optimize` (greedy APR-ranked, cash-budget constrained)
- [x] ROI calculator per invoice — annualized return of paying early — `discount_roi` (cost-of-forgoing-discount APR) + `GET /api/discounts/invoices/{id}/roi`
- [x] Bulk discount negotiations — "Pay all 10 invoices from Vendor X early for 2%" — `POST /api/discounts/bulk-negotiate` (vendor-scoped offer over open invoices)
- [x] Supplier financing marketplace — connect to supply chain finance platforms — `services/financing_adapters/` (mock default + c2fo skeleton)
- [x] Dashboard: total discounts captured, missed, and projected savings — `GET /api/discounts/dashboard` + `/discounts` web page
- [x] Auto-trigger early payment when ROI exceeds configurable threshold — `discount_auto_trigger` sweep (`AP_DISCOUNT_AUTO_CAPTURE_ROI_THRESHOLD`); accepts only — never moves money (CFO-gated payment run still funds)

---

### 4-Way Matching (with Quality Inspection)
**Status:** Shipped

Extend PO matching to include quality inspection data — critical for manufacturing.

- [x] 4-way match: invoice vs. PO vs. goods receipt vs. quality inspection
- [x] Quality inspection model — pass/fail, partial acceptance, deviation notes
- [x] Reject invoices for goods that failed inspection (`quality_hold` error)
- [x] Partial payment — pay only for accepted quantity (`accepted_quantity`)
- [x] Configurable `require_inspection` per org (`Organization.settings.matching.require_inspection`)
- [x] Exception routing when quality data is missing or mismatched (`quality_hold`)
- [x] Configurable match rules per vendor or commodity type — `services/matching_rules.resolve_match_rule` resolves `require_inspection` + amount `tolerance_pct` per-field from `settings.matching.vendor_rules[<vendor_id>]` → `commodity_rules[<gl_account>]` → org default
- [x] Integration with QMS (Quality Management Systems) — `services/qms_adapters/` (mock default + generic skeleton) + `qms_sync` background sweep + `POST /api/inspections/sync`; pulls inspection records into `quality_inspections`, idempotent on `(org, inspection_number)`

---

## Priority 12: Collaboration & Self-Service

### Embedded Supplier Chat & Collaboration
**Status:** Shipped

In-app communication between AP team and suppliers — no more email chains.

- [x] Per-invoice chat thread — AP team and supplier see the same conversation
- [x] Attach files to messages (corrected invoices, supporting docs)
- [x] @mention team members to loop them in
- [x] Supplier gets email notification with link to portal chat
- [x] Chat history persisted and linked to audit trail
- [x] Templates for common messages (missing PO, amount mismatch, payment status)
- [x] Resolution tracking — mark thread as resolved

---

### No-Code Workflow Builder
**Status:** Shipped

Visual drag-and-drop workflow builder for non-technical users.

- [x] Canvas UI — drag steps onto a flowchart
- [x] Conditional branching — "if amount > $10K, route to CFO"
- [x] Parallel paths — multiple approvers in parallel
- [x] Custom step types — webhook, email notification, delay/wait
- [x] Template library — pre-built workflows for common scenarios
- [x] Version history — compare and rollback workflow changes
- [x] Simulation mode — test a workflow with sample invoices before activating
- [x] Import/export workflow definitions as JSON

---

## Priority 13: Platform Expansion (adjacent markets)

These features expand beyond core AP automation into broader spend management. Airbase and Coupa win mid-market deals by offering all-in-one spend platforms. Consider these only after core AP gaps are closed.

### Expense Management
**Status:** In progress (foundation shipped — WF1)

Corporate expense tracking and reimbursement. Airbase, Coupa, SAP Concur, and Bill.com (Divvy) all offer this. Increasingly expected as part of a "spend management" platform.

WF1 (foundation) shipped the data model — five tenant-scoped tables
(`expenses`, `expense_reports`, `expense_policies`,
`corporate_card_transactions`, `expense_preapprovals`; migration
`0039_expense_management`) — plus the `/expenses` + `/expense-reports` API
(CRUD, receipt upload + cross-tenant-checked download, report attach/detach
with total recompute, RBAC, audit). See `backend/docs/expense-management.md`.
Policy enforcement, card import/reconciliation, pre-approval gating, and the
frontend UX land in WF2-4.

- [x] Out-of-pocket expense submission with receipt capture *(backend foundation done — WF1; UX lands in WF2)*
- [x] Corporate card transaction import and reconciliation *(WF4 — `/api/corporate-card-transactions` CSV import (idempotent on `external_txn_id`) + amount/date+merchant match-suggestions + match/unmatch/ignore/create-expense both-sides linkage; `/expenses` Cards tab)*
- [x] Expense policies — per diem, mileage rates, category limits *(WF3 — `services/expense_policy.py` engine + `/api/expense-policies` CRUD; violations on `Expense.policy_violations`)*
- [x] Pre-approval workflows for high-value expenses *(WF3 — `/api/expense-preapprovals` request + approve/reject with segregation; pre-approval-required policy rule)*
- [x] Integration with existing virtual card program *(WF4 — `POST /api/corporate-card-transactions/sync-virtual-cards` pulls charged `VirtualCard` spend into the reconciliation feed via the synthetic `vc:<provider_card_id>` external id; matched expenses get `payment_method=virtual_card`)*
- [x] Expense reporting with GL coding *(WF2 — report summary + CSV export (`/api/expenses/export`) + per-expense and bulk GL coding)*
- [x] Manager approval flow (reuse AP approval infrastructure) *(WF3 — report submit/approve/reject reusing `approval_chain.check_segregation` + a CFO-threshold gate)*

**Competitors:** Airbase (core offering), Coupa (full module), SAP Concur (industry leader), Bill.com/Divvy (corporate cards + expenses)

---

### Procurement / Requisitions
**Status:** Done — full procure-to-pay: requisitions + approval, requisition→PO conversion, catalog management + guided buying, budget tracking, and non-PO intake forms. Six tenant-scoped tables (migration `0041_procurement`), four routers (`/api/requisitions`, `/api/catalogs`, `/api/budgets`, `/api/intake`), and frontend routes (`/requisitions`, `/catalogs`, `/budgets`, `/intake`). See [procurement.md](../backend/docs/procurement.md) + the four vertical docs.

Procure-to-pay: requisitioning, PO creation, catalog management. Coupa and Basware are leaders here. Airbase offers "intake-to-procure" for software purchases.

- [x] Purchase requisition creation and approval *(`/api/requisitions` — create/submit/approve/reject/cancel state machine, RBAC + segregation-of-duties on approval, every transition audited; `services/requisition_service.py`)*
- [x] Requisition-to-PO conversion *(`POST /api/requisitions/{id}/convert-to-po` — approved-only, idempotent + `SELECT … FOR UPDATE` row-locked so a replay returns the existing PO, audited)*
- [x] Catalog management (supplier catalogs, punch-out) *(`/api/catalogs` — Catalog/CatalogItem CRUD, vendor-linked; live cXML/OCI punch-out round-trip via the `punchout_adapters` family (mock default + real cxml), `PunchoutSession` (migration 0045), secret-gated supplier cart-return endpoint, convert-to-requisition — see [procurement-catalogs.md](../backend/docs/procurement-catalogs.md))*
- [x] Guided buying — direct users to preferred vendors/contracts *(`GET /api/catalogs/guided-buying` — deterministic, no LLM: preferred catalogs → preferred vendors, active contracts → in-contract vendors, active catalog items by category/vendor/text)*
- [x] Budget tracking — spend against department/project budgets *(`/api/budgets` — dimension/period budgets with compute-on-read committed (open reqs + converted POs, no double-count) + actual (matched invoices, now matching all four dimensions incl. department/project via `Invoice.department`/`project`, migration 0044) — see [procurement-budgets.md](../backend/docs/procurement-budgets.md))*
- [x] Intake forms for non-PO spend (software, services) *(`/api/intake` — free-form `form_data` requests, open→in_review→approved/rejected lifecycle, idempotent + row-locked intake→requisition conversion; PO created via the existing req→PO flow)*

**Competitors:** Coupa (full source-to-pay), Basware (procurement suite), Airbase (intake-to-procure), Medius (basic procurement)

---

### Contract Management
**Status:** Done — full CLM: contract repository + document upload, spend-to-contract tracking, renewal alerts (background sweep), compliance monitoring (`contract_noncompliant` exception), and contract-based PO creation. `/api/contracts` + invoice link/unlink. See [contracts.md](../backend/docs/contracts.md).

Contract lifecycle management. Only enterprise tools (Coupa, Basware) have this natively. Most mid-market competitors don't.

- [x] Contract repository — upload and store contracts
- [x] Spend-to-contract tracking — link invoices to contracts
- [x] Renewal alerts — notify before contract expiry
- [x] Contract compliance monitoring — flag spend outside contract terms
- [x] Contract-based PO creation — auto-populate PO from contract terms

**Competitors:** Coupa (full CLM), Basware (moderate), Airbase (basic repository)

---

### Public Developer API & Webhooks
**Status:** Planned

The backend is a rich REST surface, but it's framed as an internal contract — CLAUDE.md notes "no OpenAPI published as the contract," and the `endpoint-inventory` skill exists precisely because integrators have no published spec. A first-class public API turns the platform into something customers and partners build on (ERP middleware, custom dashboards, RPA bots).

- [ ] API-key auth for programmatic access — per-tenant, scoped, revocable keys (hashed at rest like the supplier-portal card tokens), separate from the user-JWT path; rate-limited
- [ ] Published, versioned OpenAPI spec + a stable `/api/v1` contract surface (the `endpoint-inventory` output is the seed) with deprecation policy
- [ ] **Outbound** webhooks — let customers subscribe to events (invoice approved, payment settled, exception raised); signed payloads (reuse `webhook_security.py` HMAC), delivery retries + dead-letter, a redelivery UI. Mirror of the inbound webhook discipline (sign + dedupe)
- [ ] Developer docs + sandbox keys against the local-first stack; key-management UI in org settings
- [ ] Per-key usage metering (feeds the billing track below)

**Competitors:** Bill.com (public API + dev portal), Tipalti (API + webhooks), Coupa (open API platform)

---

### Platform Billing & Metering
**Status:** Planned

The product meters extraction usage (`ExtractionUsage`, `CardRebate`) but has no way to **bill** for the SaaS itself — plans, subscription state, usage rollups, invoices to customers. Needed before commercial launch beyond hand-managed contracts.

- [ ] Plan / subscription model (control-plane) — tiers, per-seat + usage components, trial state; tie to org provisioning
- [ ] Usage rollup — aggregate the existing `ExtractionUsage` (+ card rebates, payment volume, API calls) into billable meters per period
- [ ] Stripe Billing integration via an adapter (mock default — local-first; live key via sops, fail-closed) for subscriptions + metered usage + dunning; webhook-driven state, HMAC-verified + deduped
- [ ] Customer-facing billing surface — current plan, usage-to-date, invoices/receipts, payment method, plan changes
- [ ] Entitlement gating — feature flags per plan enforced in `deps.py` alongside RBAC

**Competitors:** standard SaaS monetization; the metering primitives (`ExtractionUsage`) already exist — this productizes them

---

### White-Label / Partner Branding
**Status:** Planned

Per-tenant theming so resellers, banks, and ERP partners can offer the platform under their own brand — a common mid-market distribution channel and an enterprise procurement ask.

- [ ] Per-tenant brand config — logo, color palette/theme tokens, product name, support + legal links (org settings + control-plane fields); the frontend already centralizes UI in `lib/components/` so theming threads through CSS custom properties
- [ ] Custom domain / subdomain support beyond `*.localhost` tenant routing (TLS + the existing `X-Tenant-Slug` resolution)
- [ ] Branded outbound surfaces — emails (ties to the localized email-catalogue work), remittance/check PDFs, the supplier portal, and PDF/CSV exports carry the tenant's brand, not the platform's
- [ ] Partner/reseller admin — a parent that manages multiple branded child tenants (relates to the deferred multi-entity / org-hierarchy work)

**Competitors:** AvidXchange + several bank-channel AP products ship white-label; a distribution lever more than a feature

---

## Done (completed features)

- [x] Multi-tenant architecture (database per tenant, subdomain routing)
- [x] JWT authentication with Redis token revocation
- [x] User management (invite, roles, self-service profile)
- [x] Role-based UI restrictions (Admin, AP Manager, AP Clerk, CFO)
- [x] Invoice CRUD with all standard fields
- [x] Invoice upload with PDF viewer
- [x] Mock AI extraction with confidence scoring
- [x] Configurable workflow engine (extraction, approval, ERP export steps)
- [x] Multi-approver support with search/pick UI
- [x] Approval thresholds (auto-approve below, require CFO above, max amount)
- [x] Approve/reject buttons with audit trail
- [x] Activity timeline in invoice modal (audit log with actor names)
- [x] Invoice warnings (duplicates, fraud flags, missing fields)
- [x] Bulk operations (delete, status change, export)
- [x] Status transition rules with valid transitions per status
- [x] ERP adapter pattern (Merge.dev + direct adapters for BC and NetSuite)
- [x] ERP webhook endpoint for status callbacks
- [x] ERP test connection button in org settings
- [x] ERP retry button in invoice modal for failed sends
- [x] ERP status display in invoice modal (document ID, error details)
- [x] Post-ERP statuses (posted_in_erp, payment_scheduled, paid)
- [x] Organization settings with per-section save (company, defaults, ERP, cards)
- [x] Payments page with tabs (Queue, History, Runs) and summary bar
- [x] Virtual card adapter pattern (Lithic + Nium) with platform/BYOK dual model
- [x] Card detail security (role-restricted, audit-logged, never cached)
- [x] Card API endpoints (generate, list, cancel, details, webhook, rebates, dashboard)
- [x] Advanced search and filtering
- [x] Export (CSV, JSON, XML — single and bulk)
- [x] Vendor management (status, verification, ERP sync, fuzzy matching, AI auto-creation)
- [x] Vendors page with verify/reject actions, ERP sync button, status filters
- [x] Vendor matching wired into invoice extraction pipeline
- [x] Vendor accepts_virtual_cards field for card payment eligibility
- [x] Seed data: 10 vendors (mixed sources/statuses), 10 invoices (linked to vendors, varied statuses)
- [x] Sidebar navigation with role-based visibility
- [x] Sidebar icons for all nav items (Admin, Organization, Workflows, etc.)
- [x] Delete user with cascade (role assignments cleaned up)
- [x] Invoice delete with full cascade (all related tables cleaned up)
- [x] Workflow ERP export step config (auto-send, format, payload options)
- [x] Workflow approval step: approver search/pick UI with chips
- [x] Workflow approval thresholds (auto-approve below, CFO above, max amount)
- [x] Self-service profile editing (name, password) in sidebar popover
- [x] AI extraction adapter pattern (Claude Vision, OpenAI GPT-4V, AWS Textract)
- [x] Platform/BYOK dual model for extraction (per-invoice billing for platform)
- [x] Per-field confidence scoring on extraction
- [x] AI GL coding (suggest GL account + cost center in extraction prompt)
- [x] Line item extraction
- [x] Extraction usage tracking for billing (ExtractionUsage model)
- [x] PO matching service (2-way and 3-way with configurable tolerance)
- [x] Exception queue with filters, resolution actions, and summary
- [x] Fraud detection (duplicate invoices, round amounts, future dates, unverified vendors)
- [x] Dashboard KPIs (pipeline, aging, vendor spend, monthly trends, upcoming payments)
- [x] Payment run creation and execution with ERP sync
- [x] Contract management (repository + upload, spend-to-contract tracking, renewal alerts, compliance monitoring, contract-based PO creation)
