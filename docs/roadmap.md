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
- [ ] Support multi-page PDFs — PyMuPDF rasterize each page, merge line-items, keep highest-confidence header fields
- [ ] Handle scanned/rotated/low-quality images — auto-deskew via PyMuPDF OSD before extraction
- [ ] Auto-approve extraction above configurable threshold — `auto_approve_threshold` on org settings; transition directly to `approved` when `overall_confidence >= threshold`
- [ ] Custom chart of accounts in extraction prompt — load the org's active `GLAccount` rows and inject as allowed values into the Claude Vision prompt; post-validate `suggested_gl_account`
- [ ] Extraction self-correction pass — after the primary extraction, a cheap secondary LLM call verifies invariants (`subtotal + tax + shipping ≈ amount`, `due_date >= invoice_date`, `sum(line_items.total) ≈ amount`). Flag inconsistencies with per-field confidence penalty or trigger a re-extract. Measurable accuracy lift at negligible cost.
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
**Status:** Done (core flow) — matching runs after every extraction and on every invoice mutation. Mismatches and missing POs route into the exception queue. Modal renders a PO Match panel with status, variance, and issues.

- [x] 2-way match: invoice vs. PO (amount, vendor)
- [x] 3-way match: invoice vs. PO vs. goods receipt (quantity received)
- [x] Configurable tolerance thresholds (default 5%)
- [x] Vendor-aware matching (PO lookup by vendor_id)
- [x] Wired into extraction/review pipeline — `services.invoice_warnings.refresh_warnings` runs `match_invoice_to_po` whenever an invoice changes; result is persisted on `invoice.po_match` (JSONB column added in migration 0006)
- [x] Match result display in invoice modal — color-coded panel (matched / mismatch / partial / no PO) with PO #, variance, issues
- [x] Routes mismatches to exception queue — `po_mismatch` exceptions auto-created, severity scaled (error for missing PO, warning for amount variance, info for partial 3-way receipt)
- [ ] PO management UI — list, view, link to invoices (read-only `GET /api/purchase-orders` exists; no detail page)
- [ ] Goods receipt UI — list, view, link to POs
- [ ] PO sync from ERP — `POST /api/purchase-orders/sync-erp` returns mock data; needs real ERP-adapter `list_pos()` method

**Files:** `backend/app/services/po_matching.py`, `backend/app/models/procurement.py`

---

### AI Auto GL Coding
**Status:** Done — included in the extraction prompt. Claude Vision suggests GL account and cost center based on vendor type and description. Auto-applied at >= 0.7 confidence.

- [x] AI suggests GL code + cost center during extraction
- [x] Confidence score per suggestion
- [x] Auto-apply above 0.7 threshold
- [x] Learn from corrections — reviewer corrections to `gl_account` / `cost_center` feed the per-vendor correction cache (see AI extraction section). Future extractions for the same vendor overlay the cached code on low-confidence suggestions.
- [ ] Custom chart of accounts per org in the prompt — inject the org's active `GLAccount` rows as allowed values so the AI can't invent codes.
- [ ] RAG-driven GL coding — at extraction time, retrieve the nearest-neighbor approved invoice (via the existing `invoice_embeddings`) and seed the prompt with its `gl_account`. Handles new vendors whose layout resembles a known one.
- [ ] Bulk re-code capability — admin tool to re-run GL suggestion across a date range / vendor set.
- [ ] GL code validation against chart of accounts — post-extraction check that `suggested_gl_account` is a live code for the org.

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
- [ ] Assignment — route to specific users based on exception type
- [ ] SLA tracking — time to resolution
- [ ] Bulk resolution for common patterns

**Files:** `backend/app/routers/exceptions.py`, `backend/app/models/exception.py`

---

### Advanced Approval Routing
**Status:** Planned — **Competitive gap: all competitors have this**

Current state: manual, specific, and auto approval strategies only. No amount-based routing, no delegation, no escalation. Every competitor offers configurable multi-level approval chains.

- [ ] Amount-based routing — auto-route to different approvers by invoice amount threshold
- [ ] Department/GL-based routing — route by cost center, GL code, or department
- [ ] Multi-level approval chains — sequential approval (clerk → manager → CFO for large invoices)
- [ ] Parallel approvals — multiple approvers review simultaneously, require all/any
- [ ] Delegation / out-of-office — designate a proxy approver with date range
- [ ] Escalation rules — auto-escalate after N hours/days without action
- [ ] Email approval — approve/reject directly from email notification without logging in
- [ ] Slack/Teams approval — approve/reject from Slack message buttons
- [ ] Segregation of duties — prevent same user from creating and approving an invoice
- [ ] Approval matrix UI — visual configuration of routing rules per org

**Competitors:** Coupa (matrix approval), Tipalti (parallel + Slack), Stampli (email/Slack), Airbase (Slack-native), Basware (conditional chains)

---

### Backend RBAC Enforcement
**Status:** Done — `require_roles(*roles)` dependency, full permission matrix applied across every router. Coverage gate in `tests/test_rbac.py` blocks regressions.

- [x] `require_roles(*roles)` dependency in `app/api/deps.py` — any-of semantics, 403 on miss, WARN-level log on denial
- [x] Endpoint-level permission mapping for all 4 roles (admin / ap_manager / ap_clerk / cfo) — see `docs/authentication.md` § RBAC
- [x] Return 403 Forbidden (not just hide UI elements)
- [x] Unit tests for `require_roles` semantics + coverage gate that fails CI if a new endpoint ships without an auth dependency
- [x] Log unauthorized access attempts at WARN level (sufficient for monitoring; persistent audit-log entries deferred to SOC 2 prep)
- [ ] Segregation of duties enforcement (approver ≠ creator) — classic AP invariant, follow-up
- [ ] Per-org custom roles — currently the 4 roles are fixed

**Files:** `backend/app/api/deps.py`, every `backend/app/api/*.py` router, `backend/tests/test_rbac.py`

---

### Enhanced Fraud Detection
**Status:** Done (basic) + semantic duplicate detection shipped

Current: exact-match duplicate check (`vendor_name + invoice_number`), semantic duplicate via embeddings, round amounts, future dates, past due, unverified vendor. Needs:

- [x] Semantic duplicate detection — cosine similarity on `invoice_embeddings` catches near-duplicates the exact-match rule misses. See `backend/docs/ai-extraction.md` § Duplicate detection.
- [ ] Vendor bank account change detection (flag recent changes to `remit_to_address` / bank info)
- [ ] LLM-based anomaly detection — feed the invoice + vendor history (last N approved invoices) to an LLM with a "is this in-pattern for this vendor?" prompt. Catches what rules can't: first-time payment method, unusual remit-to, amount 5σ from vendor mean, suspicious invoice date timing. Gated by org setting; cost is one LLM call per incoming invoice.
- [ ] Rule-based invoice amount anomaly detection (vs. vendor history) — simpler statistical fallback if LLM isn't configured
- [ ] Rush payment pattern detection
- [ ] New vendor + large amount flag
- [ ] Invoice from personal email domain flag
- [ ] Configurable fraud rules per org

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
- [ ] Early-pay discount highlighting with savings calculation
- [ ] Void/cancel payment capability — backend doesn't support it yet
- [ ] Cancel a draft run before executing — backend doesn't support it yet
- [ ] Payment remittance generation (PDF/email to vendor)
- [ ] Approval workflow on a draft run (CFO sign-off before execute)

**Files:** `backend/app/api/payments.py`, `backend/app/models/payment.py`

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
- [ ] Card list in payments page (show card payments with badges in History tab)
- [ ] Card generation in payment run — batch creation when virtual card method selected
- [ ] Vendor email notification — send card details on generation
- [ ] Rebate dashboard — monthly earnings, projected savings, YTD totals
- [ ] Supplier portal integration — secure card detail viewing for vendors

### International Payments
**Status:** Planned — **Competitive gap: Tipalti, Coupa, Basware, Airbase, Medius have this**

Current state: domestic-only payment methods. Blocks entire non-US market. Tipalti supports 196 countries and 120 currencies.

- [ ] Multi-currency payment execution — pay in vendor's local currency
- [ ] FX rate management — real-time rates, rate lock at payment creation
- [ ] Cross-border ACH (global ACH networks)
- [ ] International wire transfers (SWIFT)
- [ ] SEPA payments (EU)
- [ ] Payment corridor optimization — cheapest route per destination
- [ ] Regulatory compliance per corridor (KYC/AML)
- [ ] FX gain/loss tracking and reporting

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
- [ ] Vendor counterparty management UI — admins currently set `Vendor.bank_details.counterparty_id` directly
- [ ] Reconciliation job — periodically reconcile against the processor for missing webhooks
- [ ] Stripe Treasury / Increase / Column adapters (Modern Treasury covers the most demand)

Connect to actual payment rails for non-card payments.

- [ ] ACH integration (e.g., Dwolla, Plaid, or bank API)
- [ ] Wire transfer integration
- [ ] Check printing service (e.g., Checkeeper)
- [ ] Payment status webhooks from processor
- [ ] Bank reconciliation — import statements, auto-match

---

## Priority 4: Analytics & Reporting

### Dashboard Enhancements
**Status:** Partial (basic KPIs exist)

- [ ] Spend by vendor chart (bar/pie)
- [ ] Invoice aging chart (buckets: current, 30, 60, 90+ days)
- [ ] Processing time metrics (avg time from upload to approval, to payment)
- [ ] Approval bottleneck detection (which invoices are stuck, who's slow)
- [ ] Monthly trend lines (invoice volume, total spend)
- [ ] Discount capture rate (early-pay discounts taken vs. missed)
- [ ] Touchless rate tracking (% of invoices processed without human intervention)
- [ ] Export reports as PDF/CSV
- [ ] Scheduled report delivery via email

---

### CFO / Finance-Leader Analytics
**Status:** Planned — **Executive-buyer persona**

Dashboard Enhancements above is *operational* (for AP clerks/managers). CFOs and controllers buy on different metrics — the ones that show up in board decks and drive working-capital decisions. Separate surface because the audience and filter defaults differ (entity, period, currency, accrual vs cash).

- [ ] Days Payable Outstanding (DPO) trend — monthly/quarterly, with benchmark overlay
- [ ] Cash conversion cycle (DSO + DIO - DPO) where data available
- [ ] Accruals view — open POs × GR ÷ invoices not yet posted
- [ ] Working capital impact — "if we paid 5 days later across the board, how much cash unlocked"
- [ ] Supplier concentration — % of spend going to top 10 / top 50 vendors; flag if a single vendor exceeds threshold
- [ ] Fraud rate trend — exceptions raised / total invoices, by type
- [ ] Early-pay discount ROI — captured vs missed, dollar value of missed
- [ ] Rebate yield — virtual card rebates earned as % of spend + annualized run rate
- [ ] Forecast variance — actual AP outflow vs forecast, monthly
- [ ] Drill-through from every KPI to the contributing invoice set (don't make CFOs export CSVs to investigate)

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
**Status:** Planned

Separate portal for vendors to interact with the AP system. Biggest workflow gap — forces email/manual invoice intake without this. Every competitor (Coupa CSP, Tipalti Supplier Hub, Basware Network, Stampli) offers this.

- [ ] Vendor login (separate auth, linked to vendor record)
- [ ] Submit invoices directly (upload PDF or enter manually)
- [ ] PO flip — create invoice from PO (pre-populate fields)
- [ ] Check invoice status and payment status
- [ ] View payment history and download remittances
- [ ] Update company info, bank details, tax ID
- [ ] Bank detail change requires AP admin approval (fraud prevention)
- [ ] W-9/W-8 form upload and management
- [ ] Notification preferences (email on payment, on rejection)
- [ ] Virtual card detail viewing (secure, one-time access)
- [ ] Early payment discount offers (tie into dynamic discounting)

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
- [ ] Session management — concurrent session limits, forced logout

**Competitors:** All competitors support SSO. Coupa, SAP Concur, and Basware also support SCIM.

---

### SOC 2 Readiness
**Status:** Engineering prereqs in motion — **kickoff plan + most code controls landed; process work pending founder sign-off**

SOC 2 Type I (design) → Type II (operating over time) is the table-stakes security attestation for selling to finance teams. Full plan in [`docs/soc2-readiness.md`](soc2-readiness.md) — vendor comparison, control mapping, timeline, and what the founder still needs to do as a human.

**Engineering prerequisites:**
- [x] Access reviews — `backend/scripts/access_review.py` exports every user × role × org as CSV (quarterly)
- [x] Backup + DR runbook — `docs/backup-disaster-recovery.md` with RTO/RPO + restore procedures
- [x] Secrets rotation runbook — `docs/secrets-rotation.md` (cadence + procedure for every secret)
- [x] Vulnerability scanning in CI — Dependabot (shipped) + CodeQL SAST (Python + JS) + Trivy on the backend container, weekly + on push (`.github/workflows/security.yml`)
- [x] RBAC enforcement at API layer (separate roadmap item — already done)
- [x] MFA support + org-level enforcement (separate roadmap item — already done)
- [ ] Session management — concurrent session limits, forced logout on role change
- [ ] Centralized audit log shipping — tenant-DB `audit_log` → CloudWatch Logs / S3 Object Lock
- [ ] Auth event audit log — login/logout/MFA events into the audit log table reliably
- [ ] HSTS header + verify TLS coverage end-to-end
- [ ] KMS key auto-rotation flag in Terraform
- [ ] S3 versioning + Object Lock verified in Terraform

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
**Status:** Planned

- [ ] Email notifications on key events (invoice assigned, approved, rejected, paid)
- [ ] Configurable notification preferences per user
- [ ] Email-to-invoice — forward invoices to a dedicated email address for auto-import (Bill.com, Tipalti, Stampli, Medius have this)
- [ ] Slack/Teams integration for approval notifications (Stampli, Airbase differentiate here)
- [ ] In-app notification center

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
**Status:** Planned

One-paragraph natural-language summary at the top of the invoice modal, generated from the audit log + extraction metadata. Dramatically improves the "catching up on an invoice" UX — reviewers don't have to parse a 15-row timeline.

- [ ] Cached summary field on `invoices.meta` (regenerate on audit log mutation)
- [ ] LLM call invoked lazily on first open after audit log changes
- [ ] Handles all status transitions, corrections, exception resolutions, ERP sync events
- [ ] Shows confidence context: *"auto-extracted at 95% confidence with RAG priors applied"*
- [ ] Small feature but high-visibility — pairs well with the invoice-list priors chips

---

### Predictive Cash Flow Forecasting
**Status:** Planned

Use AP data to forecast cash outflows and optimize payment timing.

- [ ] Forecast daily/weekly/monthly cash outflows from pending invoices
- [ ] Factor in payment terms, early-pay discounts, and approval pipeline
- [ ] "What-if" scenarios — impact of paying early vs. on-time vs. late
- [ ] Cash position dashboard with AP commitments overlay
- [ ] Alert when projected outflows exceed thresholds
- [ ] Integration with bank balance data for complete cash picture
- [ ] Export forecasts for CFO reporting

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
**Status:** Partial (audit log exists, not SOX-certified)

Enhance the existing audit trail to meet SOX (Sarbanes-Oxley) compliance requirements.

- [ ] Immutable audit log — prevent any modification or deletion of audit entries
- [ ] Segregation of duties enforcement — same user can't create and approve
- [ ] Access control audit — log who viewed what, not just who changed what
- [ ] Periodic access reviews — flag users with unused elevated permissions
- [ ] Retention policies — configurable retention periods, archival
- [ ] Audit report generation — formatted for external auditors
- [ ] Digital signatures on approvals (timestamp + user hash)
- [ ] Change history on every field — before/after values
- [ ] Export audit trail per invoice or date range for auditor review

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
