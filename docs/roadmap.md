# Roadmap

Feature backlog for the AP automation platform, ordered by impact.

## Legend

- **Done** — implemented and working
- **Partial** — backend or models exist, needs completion
- **Planned** — not started

---

## Priority 1: Core Automation (highest impact)

### Real AI Extraction
**Status:** Done — adapter pattern with Claude Vision (platform), OpenAI GPT-4V, AWS Textract (BYOK). Platform/BYOK dual model. See [ai-extraction.md](ai-extraction.md).

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
- [ ] Support multi-page PDFs
- [ ] Handle scanned/rotated/low-quality images
- [ ] Auto-approve extraction above configurable threshold
- [ ] Custom chart of accounts in extraction prompt
- [ ] Learning from corrections

**Files:** `backend/app/services/extraction_adapters/`, `backend/app/services/extraction.py`

---

### 2/3-Way PO Matching & Auto-Validation
**Status:** Partial — matching service built with 2-way and 3-way logic. UI and pipeline wiring not done. See [po-matching.md](po-matching.md).

- [x] 2-way match: invoice vs. PO (amount, vendor)
- [x] 3-way match: invoice vs. PO vs. goods receipt (quantity received)
- [x] Configurable tolerance thresholds (default 5%)
- [x] Vendor-aware matching (PO lookup by vendor_id)
- [ ] Wire matching into extraction/review pipeline
- [ ] Match result display in invoice modal
- [ ] Route mismatches to exception queue
- [ ] PO management UI — list, view, link to invoices
- [ ] Goods receipt UI — list, view, link to POs
- [ ] PO sync from ERP

**Files:** `backend/app/services/po_matching.py`, `backend/app/models/procurement.py`

---

### AI Auto GL Coding
**Status:** Done — included in the extraction prompt. Claude Vision suggests GL account and cost center based on vendor type and description. Auto-applied at >= 0.7 confidence.

- [x] AI suggests GL code + cost center during extraction
- [x] Confidence score per suggestion
- [x] Auto-apply above 0.7 threshold
- [ ] Custom chart of accounts per org in the prompt
- [ ] Learn from corrections — improve over time
- [ ] Bulk re-code capability
- [ ] GL code validation against chart of accounts

---

## Priority 2: Workflow & Exceptions

### Exception Queue
**Status:** Partial (exception model exists, no UI)

Dedicated page for handling flagged invoices — mismatches, rejections, anomalies.

- [ ] Exception list page with filters (type, status, severity, date)
- [ ] Exception types: PO mismatch, duplicate, fraud flag, rejection, extraction failure
- [ ] Resolution actions: override, correct, escalate, reject
- [ ] Assignment — route to specific users based on exception type
- [ ] SLA tracking — time to resolution
- [ ] Bulk resolution for common patterns
- [ ] Link back to invoice modal from exception

**Files:** `backend/app/models/exception.py` (model exists)

---

### Enhanced Fraud Detection
**Status:** Done (basic)

Current: duplicate check, round amounts, future dates, past due. Needs:

- [ ] Vendor bank account change detection (flag recent changes)
- [ ] Invoice amount anomaly detection (vs. vendor history)
- [ ] Rush payment pattern detection
- [ ] New vendor + large amount flag
- [ ] Invoice from personal email domain flag
- [ ] Configurable fraud rules per org

---

## Priority 3: Payments

### Payment Run UI
**Status:** Partial — payments page with 3 tabs (Queue, History, Runs), summary bar, and backend endpoints done. Payment run creation/execution flow not yet implemented.

Full payment execution flow in the frontend.

- [x] Payment queue page — approved invoices sorted by due date, overdue highlighting
- [x] Payment history — all methods in one table (ACH, wire, check, card badges)
- [x] Payment runs list — batch history with status, total, count
- [x] Summary bar — total paid, pending, queue count, payments, rebates earned
- [x] Payment queue backend — `GET /api/payments/queue` and `GET /api/payments/summary`
- [ ] Early-pay discount highlighting with savings calculation
- [ ] Create payment run — select invoices, choose method, review totals
- [ ] Execute payment run — batch processing with status tracking
- [ ] Payment details modal — status, reference, method, dates
- [ ] Void/cancel payment capability
- [ ] Payment remittance generation (PDF/email to vendor)

**Files:** `backend/app/api/payments.py`, `backend/app/models/payment.py`

**See also:** [payments.md](payments.md)

---

### Virtual Card Program
**Status:** Partial — adapter pattern (Lithic + Nium), models, API endpoints, org config UI, and webhook handler done. Frontend card list page and payment run integration not yet built.

Generate single-use virtual cards per invoice payment. Earn 1-2% rebates on every card payment. Primary monetization channel. See [virtual-cards.md](virtual-cards.md) for full design.

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

### Bank / Payment Processor Integration
**Status:** Planned

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
**Status:** Planned

- [ ] Tax rate lookup by jurisdiction (e.g., Avalara, TaxJar)
- [ ] 1099 tracking for US vendors
- [ ] VAT handling for international invoices
- [ ] Withholding tax calculation
- [ ] Tax report generation

---

## Priority 6: Supplier Portal

### Vendor Self-Service
**Status:** Planned

Separate portal for vendors to interact with the AP system.

- [ ] Vendor login (separate auth, linked to vendor record)
- [ ] Submit invoices directly (upload PDF or enter manually)
- [ ] Check invoice status and payment status
- [ ] View payment history and download remittances
- [ ] Update company info, bank details, tax ID
- [ ] Bank detail change requires AP admin approval
- [ ] Notification preferences (email on payment, on rejection)

---

## Priority 7: Mobile & Notifications

### Mobile Access
**Status:** Partial (responsive CSS)

- [ ] PWA (Progressive Web App) — installable, works offline for viewing
- [ ] Push notifications for approvals needing attention
- [ ] Mobile-optimized approval flow (swipe to approve/reject)
- [ ] Camera upload — take photo of paper invoice directly

### Email & Notification System
**Status:** Planned

- [ ] Email notifications on key events (invoice assigned, approved, rejected, paid)
- [ ] Configurable notification preferences per user
- [ ] Email-to-invoice — forward invoices to a dedicated email address for auto-import
- [ ] Slack/Teams integration for approval notifications
- [ ] In-app notification center

---

## Priority 8: AI-Powered Automation (strong differentiators)

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

## Priority 9: Compliance & E-Invoicing

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
- [ ] Auto-detect format on upload — parse structured data instead of OCR
- [ ] Validate against schema — reject malformed e-invoices with clear errors
- [ ] Generate compliant e-invoices for outbound (supplier portal responses)
- [ ] Access point / PEPPOL AS4 gateway integration
- [ ] Country-specific tax validation (VAT, GST)

---

## Priority 10: Dynamic Payments & Matching

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

## Priority 11: Collaboration & Self-Service

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
