# Roadmap

Feature backlog for the AP automation platform, ordered by impact.

## Legend

- **Done** — implemented and working
- **Partial** — backend or models exist, needs completion
- **Planned** — not started

---

## Priority 1: Core Automation (highest impact)

### Real AI Extraction
**Status:** Partial (mock implementation)

Replace the mock extraction service with a real AI/OCR provider. This is the #1 value driver for touchless processing.

- [ ] Integrate Claude Vision API for invoice field extraction
- [ ] Add AWS Textract as a fallback provider
- [ ] Support multi-page PDFs
- [ ] Extract line items (not just header fields)
- [ ] Confidence scoring per field — flag low-confidence for human review
- [ ] Auto-approve extraction above configurable threshold (config exists in workflow)
- [ ] Handle scanned/rotated/low-quality images
- [ ] Target: 95%+ accuracy on standard invoice formats

**Files:** `backend/app/services/extraction.py` (replace `_mock_extract`)

---

### 2/3-Way PO Matching & Auto-Validation
**Status:** Partial (DB models exist, no logic)

Match invoices against purchase orders and goods receipts to catch discrepancies before payment.

- [ ] 2-way match: invoice vs. PO (amount, vendor, line items)
- [ ] 3-way match: invoice vs. PO vs. goods receipt (quantity received)
- [ ] Configurable tolerance thresholds (e.g., 5% amount variance allowed)
- [ ] Auto-approve if within tolerance
- [ ] Route to exception queue if mismatch
- [ ] PO management UI — list, view, link to invoices
- [ ] Goods receipt UI — list, view, link to POs
- [ ] Match status visible in invoice modal

**Files:** `backend/app/models/procurement.py` (PO, GR models exist)

---

### AI Auto GL Coding
**Status:** Planned

Use AI to automatically assign GL account and cost center based on vendor, description, line items, and historical patterns.

- [ ] Train/prompt AI with org's chart of accounts
- [ ] Suggest GL code + cost center on extraction
- [ ] Learn from corrections — improve over time
- [ ] Confidence score — auto-apply above threshold, flag below
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
**Status:** Partial (backend API exists, no frontend)

Full payment execution flow in the frontend.

- [ ] Payment queue page — approved invoices sorted by due date
- [ ] Early-pay discount highlighting with savings calculation
- [ ] Create payment run — select invoices, choose method, review totals
- [ ] Execute payment run — batch processing with status tracking
- [ ] Payment history — past runs and individual payments
- [ ] Payment details modal — status, reference, method, dates
- [ ] Void/cancel payment capability
- [ ] Payment remittance generation (PDF/email to vendor)

**Files:** `backend/app/api/payments.py`, `backend/app/models/payment.py`

**See also:** [payments.md](payments.md)

---

### Bank / Payment Processor Integration
**Status:** Planned

Connect to actual payment rails.

- [ ] ACH integration (e.g., Dwolla, Plaid, or bank API)
- [ ] Wire transfer integration
- [ ] Virtual card provider (e.g., Stripe Issuing, Marqeta)
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
- [x] Post-ERP statuses (posted_in_erp, payment_scheduled, paid)
- [x] Organization settings (company profile, invoice defaults, ERP config)
- [x] Advanced search and filtering
- [x] Export (CSV, JSON, XML — single and bulk)
- [x] Sidebar navigation with role-based visibility
