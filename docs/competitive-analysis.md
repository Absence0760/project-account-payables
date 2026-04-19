# Competitive Analysis

Analysis of the AP automation market as of April 2026. Covers 10 major competitors and maps their capabilities against our platform.

## Competitors

| Company | Target Market | Key Differentiator |
|---------|--------------|-------------------|
| **Bill.com (BILL)** | SMB to mid-market | Broad payments network, Divvy expense cards |
| **Tipalti** | Mid-market, high-volume cross-border | 196 countries, 120 currencies, mass payouts |
| **Coupa** | Enterprise (BSM platform) | $6T+ community spend intelligence, full source-to-pay |
| **SAP Concur** | Enterprise | Industry-leading T&E, deep SAP ecosystem |
| **AvidXchange** | Mid-market (real estate, construction) | 200+ vertical-specific ERP integrations |
| **MineralTree** | Mid-market | ERP-first philosophy, TotalPay payment network |
| **Stampli** | Mid-market | Invoice-centric collaboration, "Billy the Bot" AI |
| **Airbase** | Mid-market | All-in-one spend management (AP + expenses + cards) |
| **Medius** | Mid-market to enterprise | Legacy Readsoft OCR heritage, strong EU presence |
| **Basware** | Enterprise | E-invoicing leader (Peppol), 97% touchless for PO invoices |

---

## Feature Comparison Matrix

**Legend:** Have = we have it, Partial = partially built, Gap = we don't have it

### Core AP Automation

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| AI invoice extraction | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Multi-provider extraction (BYOK) | **Have** | - | - | - | - | - | - | - | - | - | - |
| Per-field confidence scoring | Have | - | - | Y | - | - | - | Y | - | Y | Y |
| Invoice workflows/state machine | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| PO matching (2-way) | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| PO matching (3-way) | Have | - | Y | Y | Y | - | Y | Y | Y | Y | Y |
| Duplicate detection (exact match) | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Semantic duplicate detection (embeddings) | **Have** | - | - | - | - | - | - | - | - | - | - |
| AI learning from corrections | **Have** | - | - | - | - | - | - | Y | - | - | - |
| RAG-based few-shot extraction priors | **Have** | - | - | - | - | - | - | - | - | - | - |
| Per-vendor correction cache | **Have** | - | - | - | - | - | - | Y | - | - | - |
| Exception queue | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Audit trail | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Bulk operations | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Self-service tenant signup | **Have** | Y (SMB) | - | - | - | - | - | - | - | - | - |
| First-login password change enforcement | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |

### Approvals

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| Basic approval routing | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Amount-based routing | Partial | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Multi-level chains | Gap | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Parallel approvals | Gap | - | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Delegation / out-of-office | Gap | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Escalation rules | Gap | - | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Email/Slack approval | Gap | Y | Y | Y | Y | Y | Y | Y | Y | - | - |
| Segregation of duties | Gap | - | Y | Y | - | - | Y | Y | Y | Y | Y |

### Payments

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| ACH | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Wire (domestic) | Have | Y | Y | Y | Y | - | Y | Y | Y | Y | Y |
| Check | Have | Y | Y | Y | Y | Y | Y | Y | Y | - | - |
| Virtual cards | Have | Y | - | Y | Y | Y | Y | Y | Y | Y | Y |
| Payment runs (batch) | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| International/cross-border | Gap | Y | Y | Y | Y | - | - | Y | Y | Y | Y |
| Multi-currency FX | Gap | - | Y | Y | Y | - | - | - | Y | Y | Y |
| Dynamic discounting | Gap | - | Y | Y | - | Y | - | Y | - | Y | Y |
| Payment reconciliation | Gap | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |

### Virtual Cards (our strength)

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| Card issuance | Have | Y | - | Y | Y | Y | Y | Y | Y | Y | Y |
| Multi-provider (Lithic + Nium) | **Have** | - | - | - | - | - | - | - | - | - | - |
| Rebate tracking | Have | - | - | Y | - | - | Y | - | - | Y | Y |
| Multi-region auto-selection | **Have** | - | - | - | - | - | - | - | - | - | - |
| Platform/BYOK dual model | **Have** | - | - | - | - | - | - | - | - | - | - |

### ERP Integration

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| Pre-built ERP connectors | Have (10) | 5 | 6 | 8+ | 6+ | 200+ | 6 | 70+ | 3 | 8+ | 10+ |
| Pluggable adapter pattern | **Have** | - | - | Y | - | - | - | - | - | - | Y |
| Bi-directional sync | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Webhook status callbacks | Have | - | Y | Y | Y | - | - | Y | - | Y | Y |
| Connection test UI | Have | Y | Y | Y | Y | - | - | Y | - | Y | Y |
| Unified API (Merge.dev) | **Have** | - | - | Y | - | - | - | - | - | - | - |

### Vendor Management

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| Vendor CRUD | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Status lifecycle | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| AI auto-creation from invoices | **Have** | - | - | - | - | - | - | Y | - | - | - |
| Fuzzy matching | Have | - | - | Y | - | - | - | Y | - | Y | Y |
| ERP vendor sync | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| W-9 collection | Gap | Y | Y | Y | - | Y | Y | Y | Y | - | - |
| Sanctions/OFAC screening | Gap | - | Y | Y | - | - | - | - | - | Y | Y |
| Supplier portal | Gap | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |

### Compliance & Security

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| RBAC (UI-level) | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| RBAC (API-level enforcement) | Gap | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| SSO/SAML | Gap | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| MFA | Gap | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| SOC 2 Type I/II | Gap | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| hCaptcha on public endpoints | **Have** | - | - | - | - | - | - | - | - | - | - |
| Rate limiting (Redis sliding window) | **Have** | - | - | Y | - | - | - | - | - | Y | - |
| SOPS + KMS encrypted secrets | **Have** | - | - | - | - | - | - | - | - | - | - |
| 1099 e-filing | Gap | Y | Y | - | - | Y | Y | Y | Y | Y | - |
| VAT/GST handling | Gap | - | Y | Y | Y | - | - | - | - | Y | Y |
| E-invoicing (Peppol, etc.) | Gap | - | - | - | - | - | - | - | - | Y | Y |

### Architecture & Multi-Tenancy

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| Database-per-tenant isolation | **Have** | - | - | - | - | - | - | - | - | - | - |
| Multi-entity within tenant | Gap | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Subdomain-based routing | Have | - | - | - | - | - | - | - | - | - | - |
| Intercompany transactions | Gap | - | Y | Y | Y | - | - | - | Y | Y | Y |

### Analytics & Reporting

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| Dashboard KPIs | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Spend by vendor | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Aging reports | Have | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Cash flow forecasting | Gap | Y | - | Y | - | - | - | - | - | Y | Y |
| Custom report builder | Gap | - | Y | Y | Y | - | - | Y | Y | Y | Y |
| Scheduled report delivery | Gap | - | Y | Y | Y | - | - | - | - | Y | Y |
| Touchless rate tracking | Gap | - | - | Y | - | - | - | - | - | Y | Y |

### Platform / Adjacent Features

| Feature | Us | Bill | Tipalti | Coupa | Concur | Avid | Mineral | Stampli | Airbase | Medius | Basware |
|---------|-----|------|---------|-------|--------|------|---------|---------|---------|--------|---------|
| Mobile app (iOS + Android) | **Have** | Y | - | Y | Y | Y | - | Y | Y | Y | Y |
| Camera OCR capture | **Have** | Y | - | - | - | - | - | Y | - | - | - |
| Biometric login (Face ID / fingerprint) | **Have** | Y | - | - | - | - | - | - | - | - | - |
| Offline mode (SQLite cache) | **Have** | - | - | - | - | - | - | - | - | - | - |
| Swipe-to-approve gesture | **Have** | - | - | - | - | - | - | - | - | - | - |
| Expense management | Gap | Y* | - | Y | Y | - | - | - | Y | Y | Y |
| Procurement / requisitions | Gap | - | - | Y | Y* | - | - | - | Y | Y | Y |
| Contract management | Gap | - | - | Y | Y* | - | - | - | - | - | Y |
| Email inbox ingestion | Gap | Y | Y | Y | Y | Y | - | Y | Y | Y | Y |
| Slack/Teams integration | Gap | - | Y | - | - | - | - | Y | Y | - | - |

*Bill.com expense via Divvy acquisition; SAP Concur procurement/contracts via Ariba integration

---

## Our Competitive Advantages

These are areas where we are genuinely ahead of most or all competitors:

1. **Multi-provider AI extraction with BYOK** — Every competitor uses a single proprietary OCR/AI pipeline. Our pluggable adapter pattern (Claude Vision, GPT-4V, Textract, Ollama) with both platform and bring-your-own-key models is unique. Customers aren't locked into one AI provider and can self-host via Ollama for data-sovereignty-conscious deployments.

2. **Two-layer learning from corrections** — Per-vendor correction cache (deterministic) + pgvector RAG with `text-embedding-3-small` (semantic). Reviewer corrections feed both stores simultaneously. Only Stampli has *any* correction-learning ("Billy the Bot"); nobody else has the dual deterministic + semantic approach, and nobody else exposes the priors transparently in the UI (the "Extraction priors" panel shows exactly which past invoices and cached fields shaped each extraction).

3. **Semantic duplicate detection via embeddings** — Reuses the same `invoice_embeddings` store to catch near-duplicates the rule-based (vendor_name + invoice_number) check misses. Configurable threshold, no extra compute. Every competitor has rule-based dup detection; nobody catches OCR-drift or template-resend cases this cleanly.

4. **Database-per-tenant isolation** — Most competitors use row-level tenant isolation. Our database-per-tenant architecture provides stronger security guarantees, easier compliance (data residency), and simpler tenant lifecycle management. Strong selling point for security-conscious enterprise buyers.

5. **Self-service tenant provisioning** — Anonymous visitor → signup form → email verification → provisioned tenant with temp password → first-login password change, fully automated in ~30s. Every enterprise competitor (Tipalti, Coupa, Concur, Medius, Basware, Stampli, Airbase) is sales-led only. Bill.com has SMB self-serve; our implementation with hCaptcha + Redis rate limiting + two-phase (start → verify → complete) is production-ready PLG infrastructure.

6. **Multi-provider virtual cards** — Lithic + Nium with automatic region-based provider selection. Most competitors partner with a single card issuer. Our multi-provider approach gives better global coverage and negotiating leverage.

7. **Native mobile app with differentiated features** — Flutter iOS + Android with camera OCR capture, biometric login (Face ID / fingerprint / device PIN), offline mode (SQLite cache), push notifications (FCM), and swipe-to-approve. Most AP competitors have browser-first mobile or basic approval-only apps. Offline mode and swipe-to-approve are genuinely rare.

8. **Pluggable adapter architecture** — Extraction, ERP, card, email, and embedding providers are all pluggable via decorator-based registration. Adding a new provider means copying a file and implementing the interface. More developer-friendly and extensible than any enterprise AP tool.

9. **Unified ERP API (Merge.dev) + direct adapters** — Best of both worlds: broad coverage via Merge.dev's unified API with the option to build optimized direct integrations for high-value ERPs.

10. **AI vendor auto-creation with fuzzy matching** — Vendors are automatically created from invoice extraction with confidence scoring and fuzzy name matching. Most competitors require manual vendor setup before invoice processing.

11. **Production-grade security infrastructure out of the box** — SOPS + AWS KMS for secrets encryption (committed encrypted to git), Dependabot for supply-chain monitoring, hCaptcha on anonymous endpoints, Redis-backed sliding-window rate limiting, JWT with blocklist on logout. Competitors often treat these as enterprise-tier features.

---

## Critical Gaps to Close

Ranked by competitive impact — features where we are behind most or all competitors. Matches the prioritization in `roadmap.md`.

### Tier 1: Deal-Blockers (hard filter on every mid-market+ RFP)

| Gap | Why It Matters | Who Has It |
|-----|---------------|-----------|
| **SSO (SAML + OIDC + SCIM)** | No SSO = no enterprise sale, period. Fields exist in User model but not wired. | All competitors |
| **PO matching pipeline wiring** | 2-way + 3-way matching *service* exists; extraction/review UI doesn't call it and exceptions aren't routed. Mid-market buyers expect PO-gated invoices. | All competitors |
| **Real ACH / wire execution** | Virtual cards cover ~30-40% of spend in practice; the rest is ACH. Need Modern Treasury / Stripe Treasury / direct bank integration. | All competitors |
| **Supplier portal** | Vendors can't self-submit invoices or check payment status. Forces email/manual intake. Biggest workflow gap. | All competitors |
| **SOC 2 Type II** | Security review blocker. Mostly process + docs, but a few engineering controls (access reviews, centralized audit shipping, encryption verification). 60+ day blocker if not started. | All competitors |
| **Backend RBAC enforcement** | API endpoints aren't gated by role — security vulnerability. Any authenticated user can hit any endpoint. | All competitors |
| **1099 tax compliance** | W-9 collection, TIN validation, 1099 e-filing. Required for US AP operations. | Bill, Tipalti, Avid, Mineral, Stampli, Airbase |

### Tier 2: Competitive Disadvantages (lose deals against peers)

| Gap | Why It Matters | Who Has It |
|-----|---------------|-----------|
| **Advanced approval routing** | No amount-based auto-routing, no delegation, no escalation, no parallel chains. Workflow engine has the state machine but not the rules. | All competitors |
| **International payments (FX + local rails)** | Multi-currency, cross-border ACH/wire, SEPA, SWIFT. Blocks non-US market entirely. | Tipalti, Coupa, Concur, Airbase, Medius, Basware |
| **Multi-entity / intercompany** | Consolidated reporting, cross-entity allocations. Blocks mid-market customers with subsidiaries. | All enterprise competitors |
| **CFO / finance-leader analytics** | Current dashboard is operational (aging, touchless rate). Missing DPO, cash conversion cycle, accruals, supplier concentration — the KPIs CFOs buy on. | Coupa, Tipalti, Basware, Medius |
| **Segregation of duties** | Same user can create and approve invoices. Compliance requirement for regulated industries. | Tipalti, Coupa, MineralTree, Stampli, Airbase, Medius, Basware |
| **Sanctions/OFAC screening** | Required for regulated industries. Vendors aren't screened against sanctions lists. | Tipalti, Coupa, Medius, Basware |
| **Custom report builder** | Fixed KPIs only. Mid-market+ buyers expect ad-hoc reporting. | Coupa, Tipalti, Stampli, Airbase, Medius, Basware |

### Tier 3: Market Expansion (opens new segments)

| Gap | Why It Matters | Who Has It |
|-----|---------------|-----------|
| **E-invoicing compliance (Peppol, CFDI, NFe)** | EU mandates through 2026-2028; LatAm already mandated. Without Peppol/ViDA readiness, no EU market. | Medius, Basware |
| **Expense management** | Adjacent market. All-in-one spend platforms are winning mid-market. | Bill/Divvy, Coupa, Concur, Airbase, Medius |
| **Dynamic discounting** | Revenue opportunity. Optimize payment timing for discounts. | Coupa, Basware, Medius, Tipalti |
| **Email inbox ingestion** | Auto-import invoices from a dedicated email address. Reduces manual upload. | Bill, Tipalti, Coupa, Stampli, Medius, Basware |
| **Procurement** | Full procure-to-pay. Requisitioning, catalogs, guided buying. | Coupa, Basware, Airbase, Medius |
| **Contract management** | Spend-to-contract tracking, renewal alerts. | Coupa, Basware |

---

## Competitor Deep Dives

### Bill.com (BILL)

**Strengths:** Dominant in SMB/mid-market. Strong payments network. Divvy acquisition added expense management and corporate cards. Good QuickBooks/Xero integration. 1099 e-filing built in.

**Weaknesses:** Limited international payments. Basic PO matching. Not enterprise-grade. Limited AI beyond basic OCR. No procurement or contract management.

**Where we win:** Multi-provider AI extraction with BYOK, two-layer correction learning (cache + RAG), semantic duplicate detection, database-per-tenant security, pluggable architecture, stronger ERP breadth, native mobile with offline + biometric.

**Where they win:** Supplier portal, 1099 compliance, expense management (Divvy), broader SMB market penetration, established payments network, brand recognition.

---

### Tipalti

**Strengths:** Best-in-class international payments (196 countries, 120 currencies). Strong supplier onboarding portal. OFAC/sanctions screening. Comprehensive 1099/W-8 tax compliance. Deep NetSuite integration.

**Weaknesses:** No expense management. Limited mobile. Procurement is basic. Not a full BSM platform.

**Where we win:** Multi-provider AI extraction with BYOK, two-layer correction learning, semantic duplicate detection, database-per-tenant isolation, virtual card multi-provider approach, pluggable adapter architecture, self-service signup, modern native mobile app.

**Where they win:** International payments, tax compliance, sanctions screening, supplier portal, advanced approval routing with Slack integration, SOC 2 attestation.

---

### Coupa

**Strengths:** Full Business Spend Management platform. Community intelligence from $6T+ spend data. Strongest procurement suite. Dynamic discounting and supply chain financing. Deep SAP integration. Enterprise-grade everything.

**Weaknesses:** Complex and expensive. Overkill for SMB/mid-market. Long implementation cycles. Not AI-native (improving).

**Where we win:** AI-native extraction (BYOK model), faster time-to-value, simpler architecture, better developer experience, more affordable mid-market positioning.

**Where they win:** Everything at enterprise scale — procurement, contract management, community fraud detection, analytics, global compliance, expense management.

---

### Stampli

**Strengths:** Invoice-centric collaboration. "Billy the Bot" AI that learns from corrections. 70+ ERP integrations. Strong mid-market positioning. Good approval UX.

**Weaknesses:** No expense management. No procurement. Limited international payments. Basic tax compliance.

**Where we win:** Multi-provider AI (vs single proprietary), transparent priors UI (reviewer sees exactly which past invoices shaped extraction), semantic duplicate detection, database-per-tenant isolation, virtual card multi-provider, broader ERP coverage via Merge.dev, native mobile with offline + biometric.

**Where they win:** Longer track record with correction-learning (Billy the Bot is mature), invoice collaboration threads, supplier portal, Slack approval integration, more mature approval routing, SOC 2.

---

### Airbase

**Strengths:** All-in-one spend management (AP + expenses + cards). Real-time spend controls. Strong Slack integration. Good NetSuite/Intacct integration. Procurement intake workflows.

**Weaknesses:** Limited ERP breadth (3 deep integrations). No international specialization. Newer entrant — less mature AP automation. No contract management.

**Where we win:** Broader ERP coverage, multi-provider AI extraction, database-per-tenant isolation, virtual card multi-provider.

**Where they win:** Expense management, corporate cards, Slack-native approvals, procurement intake, real-time spend controls, SOC 2.

---

### Medius (formerly Readsoft)

**Strengths:** Legacy OCR heritage (best-in-class document capture). Strong European presence. Good SAP/Dynamics integration. Dynamic discounting. E-invoicing compliance. Fraud detection with community intelligence.

**Weaknesses:** Less known in US market. UI/UX not as modern. Limited mobile. Not a full BSM platform.

**Where we win:** Multi-provider AI (vs proprietary OCR), modern tech stack, BYOK model, database-per-tenant isolation.

**Where they win:** E-invoicing compliance, VAT/global tax, dynamic discounting, fraud detection maturity, European market coverage, deeper SAP integration.

---

### Basware

**Strengths:** E-invoicing leader (Peppol network). Claims 97% touchless processing for PO invoices. Strong global compliance. Basware Network (supplier connectivity). Comprehensive procurement. Good SAP/Oracle integration.

**Weaknesses:** Expensive. Complex implementation. Less modern UX. Slower innovation cycle. Limited US mid-market presence.

**Where we win:** Modern architecture, AI-native extraction, faster deployment, BYOK model, database-per-tenant isolation, mid-market pricing.

**Where they win:** E-invoicing compliance, global tax, dynamic discounting, procurement, contract management, touchless processing rate, Basware Network.

---

## Strategic Positioning

### Our sweet spot
**SMB-to-mid-market PLG segment (20-1,000 employees)** that wants:
- AI-first invoice processing with transparent learning (not legacy OCR, not black-box "AI")
- Strong ERP integration without enterprise pricing or sales cycles
- Self-service onboarding — spin up a workspace in 30 seconds without talking to sales
- Virtual card rebates that offset or exceed platform cost
- Tenant-isolated security (database-per-tenant) for compliance without enterprise-tier pricing
- Native mobile with offline/biometric for approvers

### Wedge strategy
**Self-service + AI-native differentiation at the SMB/mid-market boundary.** Enterprise competitors (Coupa, Tipalti, Basware, Medius, Stampli, Airbase) are all sales-led and take weeks-to-months to onboard. Bill.com owns SMB self-serve but has weak AI and security. Our combination — self-service provisioning + dual-layer correction learning + transparent priors UI + multi-provider BYOK AI + database-per-tenant — occupies a position nobody currently holds.

### Who we compete with most directly
**Stampli**, **Bill.com**, **MineralTree** in mid-market AP automation. We differentiate on: multi-provider AI with BYOK, two-layer learning (cache + RAG), semantic dup detection, self-service onboarding, native mobile, and tenant isolation. Stampli matches us on correction-learning (Billy the Bot) but not on transparency or semantic dup detection; Bill.com matches on SMB self-serve but not on AI depth.

### Who we compete with less directly (and why)
**Tipalti** wins international payments and tax compliance. We lose those deals today; our SSO+ACH+SOC 2 work closes the gap. **Airbase** wins expense+AP bundled. Different wedge — we'd need to build expense mgmt to compete head-on, which isn't cheap.

### Who we don't compete with (yet)
**Coupa** and **Basware** in enterprise BSM — we lack procurement, contract management, and global compliance to play at that level. **SAP Concur** in T&E-centric enterprises — different buyer.
