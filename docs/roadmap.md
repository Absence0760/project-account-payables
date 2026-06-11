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
**Status:** Partial (currency field exists, no conversion)

- [ ] Real-time exchange rate lookup (e.g., Open Exchange Rates API)
- [ ] Auto-convert to reporting currency
- [ ] Realized/unrealized gain/loss tracking
- [ ] Currency displayed correctly per locale

### Multi-Entity
**Status:** Partial (multi-tenant exists, not multi-entity within org)

- [ ] Multiple entities (subsidiaries) within one organization
- [ ] Entity-level chart of accounts, GL codes, cost centers
- [ ] Inter-company invoice routing
- [ ] Consolidated reporting across entities

### Tax Compliance
**Status:** Planned — **Competitive gap: 1099 is table stakes for US AP**

1099 compliance is required for US AP operations. Bill.com, Tipalti, AvidXchange, Stampli, MineralTree all have it. VAT/e-invoicing is required for EU expansion (Medius and Basware lead).

**US Tax (Priority):**
- [ ] W-9 collection — request, store, and validate vendor W-9 forms
- [ ] TIN validation — verify Tax Identification Numbers against IRS database
- [ ] 1099 tracking — flag vendors exceeding $600 annual threshold
- [ ] 1099-NEC and 1099-MISC generation — auto-generate from payment data
- [ ] 1099 e-filing — file electronically with IRS (direct or via partner like Tax1099)
- [ ] 1099 vendor dashboard — summary of all 1099-eligible vendors and YTD totals

**International Tax:**
- [ ] Tax rate lookup by jurisdiction (e.g., Avalara, TaxJar)
- [ ] VAT handling for international invoices
- [ ] Withholding tax calculation
- [ ] GST handling (Australia, India, Canada)
- [ ] Tax report generation
- [ ] Country-specific tax rules engine

**Competitors:** Tipalti (1099 + W-8BEN + VAT), Bill.com (1099 e-filing), Basware (global VAT, 60+ countries), Medius (EU e-invoicing mandates)

---

## Priority 6: Supplier Portal
**Competitive gap: all competitors have a supplier portal**

### Vendor Self-Service
**Status:** Partial — Phase 1 MVP shipped (separate auth, invoice submission, status/payment tracking). See [`backend/docs/supplier-portal.md`](../backend/docs/supplier-portal.md).

Separate portal for vendors to interact with the AP system. Biggest workflow gap — forces email/manual invoice intake without this. Every competitor (Coupa CSP, Tipalti Supplier Hub, Basware Network, Stampli) offers this.

- [x] Vendor login (separate auth, linked to vendor record) — `VendorUser` tenant-scoped, JWT `typ=vendor` prevents cross-contamination with AP-app tokens
- [x] Submit invoices directly (upload PDF) — routes into the existing extraction pipeline with `vendor_id` pre-filled and a `source=supplier_portal` audit breadcrumb
- [x] Check invoice status and payment status
- [x] View payment history — joins `payments` ↔ `invoices` on `vendor_id`
- [x] Admin invite flow — `POST /api/vendors/{id}/portal-users` mints a temp password + welcome email
- [ ] PO flip — create invoice from PO (pre-populate fields)
- [ ] Download remittances (PDF generation)
- [ ] Update company info, bank details, tax ID
- [ ] Bank detail change requires AP admin approval (fraud prevention)
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
**Status:** OIDC + SCIM /Users shipped · SAML + SCIM /Groups planned

No SSO = no enterprise sale. OIDC (Okta + Entra) + SCIM 2.0 user provisioning are live; SAML is a separate code path for regulated buyers that require it. See [`docs/authentication.md`](authentication.md) § SSO and § SCIM for the full design.

- [x] OIDC (OpenID Connect) support — single flow covers Okta + Entra via per-tenant discovery URL
- [x] JIT (Just-In-Time) user provisioning from SSO — match by `(provider, sub)` then `(org, email)`, otherwise create
- [x] SCIM 2.0 `/Users` provisioning (create / list / get / PATCH / soft-delete) with per-tenant bearer token
- [x] Force password change on first login (non-SSO users) — `User.must_change_password` flag, cleared on `/api/auth/change-password`
- [ ] SAML 2.0 SSO (Okta, Azure AD, OneLogin) — separate code path for regulated buyers
- [ ] SCIM `/Groups` — needs IdP-group → Role mapping design (per-tenant config? convention?)
- [ ] SSO-only mode — disable password login when SSO is configured (flag on `Organization.settings.sso`)
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
**Status:** Planned

AI agents that autonomously resolve common exceptions without human intervention — mismatched amounts, missing PO references, GL coding errors.

- [ ] Agent framework — define rules + AI fallback for each exception type
- [ ] Auto-resolve: small amount mismatches within tolerance (adjust and approve)
- [ ] Auto-resolve: missing PO — match by vendor + amount + date range
- [ ] Auto-resolve: GL coding errors — correct based on historical patterns
- [ ] Escalation rules — when agent confidence is low, route to human
- [ ] Agent decision log — full audit trail of what the agent decided and why
- [ ] Dashboard: agent resolution rate, accuracy, escalation rate
- [ ] Configurable autonomy level per org (conservative → aggressive)

---

### Adaptive AI Workflows
**Status:** Planned

Workflows that learn from team behavior and adapt over time — routing, approval timing, exception handling.

- [ ] Learn approval patterns — who approves what, how fast, rejection rates
- [ ] Smart routing — assign invoices to the fastest/most-appropriate approver
- [ ] Auto-adjust thresholds — raise auto-approve limit as accuracy improves
- [ ] Anomaly detection — flag invoices that deviate from learned patterns
- [ ] Suggest workflow changes — "Invoices from Vendor X are always approved, consider auto-approve"
- [ ] A/B testing for workflow rules — compare performance of different configs
- [ ] Feedback loop — corrections feed back into the AI model

---

### Intelligent Data Enrichment from Supplier History
**Status:** Planned

Auto-populate and validate invoice fields using historical data from the same supplier.

- [ ] Auto-fill GL account, cost center, payment terms from vendor history
- [ ] Flag deviations — "This vendor usually invoices ~$5K, this one is $50K"
- [ ] Vendor performance scoring — on-time delivery, invoice accuracy, dispute rate
- [ ] Suggest vendor consolidation — identify duplicate/similar vendors
- [ ] Enrich vendor data from external sources (D&B, Clearbit)
- [ ] Price variance detection — same item, different price across invoices

---

### Conversational AP Assistant
**Status:** Planned — **Differentiator for CFO / AP Manager persona**

Chat sidebar over the tenant's data. Replaces ad-hoc SQL and spreadsheet exports for common operational questions.

- [ ] Tool-calling LLM with a fixed toolset: `list_invoices(filters)`, `get_vendor_spend(period)`, `list_pending_approvals(assignee)`, `get_payment_forecast(horizon)`, `find_invoices_by_text(query)` — no raw SQL exposure, each tool is a typed endpoint.
- [ ] Tenant-scoped context — conversation history per user, org-level cap on tokens/cost.
- [ ] Streaming responses via server-sent events; charts rendered from structured tool output.
- [ ] Example prompts built into the empty state: *"which approvals have I been sitting on > 5 days?"*, *"which vendors are we paying the most this quarter?"*, *"show me invoices with PO mismatches over $10k"*.
- [ ] Cost controls — token budget per org per month, clear usage meter in the UI.
- [ ] Audit trail — log every tool call for compliance / debugging.

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
**Status:** Planned — **Competitive gap for regulated industries**

Tipalti, Coupa, Medius, and Basware all screen vendors against sanctions lists. Required for financial services, government contractors, and regulated industries.

- [ ] OFAC/SDN sanctions screening on vendor creation and update
- [ ] Ongoing monitoring — re-screen vendors periodically (daily/weekly)
- [ ] Flag and block payments to sanctioned entities
- [ ] Adverse media screening
- [ ] Vendor risk scoring (sanctions + fraud signals + payment history)
- [ ] Integration with screening providers (Dow Jones, Refinitiv, ComplyAdvantage)
- [ ] Screening audit trail — log all checks and results

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
**Status:** Planned

Support structured electronic invoice formats required in the EU, Australia, and other regions.

- [ ] Peppol BIS Billing 3.0 — receive and send via Peppol network
- [ ] Factur-X / ZUGFeRD — hybrid PDF/XML format (EU standard)
- [ ] UBL (Universal Business Language) 2.1 — parse and generate
- [ ] FatturaPA — Italian e-invoicing format
- [ ] CFDI 4.0 — Mexican e-invoicing (SAT stamping, UUID, PAC integration)
- [ ] NFe / NFS-e — Brazilian electronic invoicing (state-level SEFAZ integration)
- [ ] DIAN — Colombian e-invoicing
- [ ] Auto-detect format on upload — parse structured data instead of OCR
- [ ] Validate against schema — reject malformed e-invoices with clear errors
- [ ] Generate compliant e-invoices for outbound (supplier portal responses)
- [ ] Access point / PEPPOL AS4 gateway integration
- [ ] Country-specific tax validation (VAT, GST, IVA)

---

## Priority 11: Dynamic Payments & Matching

### Dynamic Discounting & Early Payment Optimization
**Status:** Planned

Go beyond static early-pay discounts — dynamically negotiate and optimize payment timing.

- [ ] Supplier-offered dynamic discounts — "Pay in 5 days for 3% off" (sliding scale)
- [ ] AI-optimized payment timing — maximize discount capture vs. cash preservation
- [ ] ROI calculator per invoice — annualized return of paying early
- [ ] Bulk discount negotiations — "Pay all 10 invoices from Vendor X early for 2%"
- [ ] Supplier financing marketplace — connect to supply chain finance platforms
- [ ] Dashboard: total discounts captured, missed, and projected savings
- [ ] Auto-trigger early payment when ROI exceeds configurable threshold

---

### 4-Way Matching (with Quality Inspection)
**Status:** Planned

Extend PO matching to include quality inspection data — critical for manufacturing.

- [ ] 4-way match: invoice vs. PO vs. goods receipt vs. quality inspection
- [ ] Quality inspection model — pass/fail, partial acceptance, deviation notes
- [ ] Reject invoices for goods that failed inspection
- [ ] Partial payment — pay only for accepted quantity
- [ ] Configurable match rules per vendor or commodity type
- [ ] Integration with QMS (Quality Management Systems)
- [ ] Exception routing when quality data is missing or mismatched

---

## Priority 12: Collaboration & Self-Service

### Embedded Supplier Chat & Collaboration
**Status:** Planned

In-app communication between AP team and suppliers — no more email chains.

- [ ] Per-invoice chat thread — AP team and supplier see the same conversation
- [ ] Attach files to messages (corrected invoices, supporting docs)
- [ ] @mention team members to loop them in
- [ ] Supplier gets email notification with link to portal chat
- [ ] Chat history persisted and linked to audit trail
- [ ] Templates for common messages (missing PO, amount mismatch, payment status)
- [ ] Resolution tracking — mark thread as resolved

---

### No-Code Workflow Builder
**Status:** Partial (configurable steps exist, not drag-and-drop)

Visual drag-and-drop workflow builder for non-technical users.

- [ ] Canvas UI — drag steps onto a flowchart
- [ ] Conditional branching — "if amount > $10K, route to CFO"
- [ ] Parallel paths — multiple approvers in parallel
- [ ] Custom step types — webhook, email notification, delay/wait
- [ ] Template library — pre-built workflows for common scenarios
- [ ] Version history — compare and rollback workflow changes
- [ ] Simulation mode — test a workflow with sample invoices before activating
- [ ] Import/export workflow definitions as JSON

---

## Priority 13: Platform Expansion (adjacent markets)

These features expand beyond core AP automation into broader spend management. Airbase and Coupa win mid-market deals by offering all-in-one spend platforms. Consider these only after core AP gaps are closed.

### Expense Management
**Status:** Planned

Corporate expense tracking and reimbursement. Airbase, Coupa, SAP Concur, and Bill.com (Divvy) all offer this. Increasingly expected as part of a "spend management" platform.

- [ ] Out-of-pocket expense submission with receipt capture
- [ ] Corporate card transaction import and reconciliation
- [ ] Expense policies — per diem, mileage rates, category limits
- [ ] Pre-approval workflows for high-value expenses
- [ ] Integration with existing virtual card program
- [ ] Expense reporting with GL coding
- [ ] Manager approval flow (reuse AP approval infrastructure)

**Competitors:** Airbase (core offering), Coupa (full module), SAP Concur (industry leader), Bill.com/Divvy (corporate cards + expenses)

---

### Procurement / Requisitions
**Status:** Planned

Procure-to-pay: requisitioning, PO creation, catalog management. Coupa and Basware are leaders here. Airbase offers "intake-to-procure" for software purchases.

- [ ] Purchase requisition creation and approval
- [ ] Requisition-to-PO conversion
- [ ] Catalog management (supplier catalogs, punch-out)
- [ ] Guided buying — direct users to preferred vendors/contracts
- [ ] Budget tracking — spend against department/project budgets
- [ ] Intake forms for non-PO spend (software, services)

**Competitors:** Coupa (full source-to-pay), Basware (procurement suite), Airbase (intake-to-procure), Medius (basic procurement)

---

### Contract Management
**Status:** Planned

Contract lifecycle management. Only enterprise tools (Coupa, Basware) have this natively. Most mid-market competitors don't.

- [ ] Contract repository — upload and store contracts
- [ ] Spend-to-contract tracking — link invoices to contracts
- [ ] Renewal alerts — notify before contract expiry
- [ ] Contract compliance monitoring — flag spend outside contract terms
- [ ] Contract-based PO creation — auto-populate PO from contract terms

**Competitors:** Coupa (full CLM), Basware (moderate), Airbase (basic repository)

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
