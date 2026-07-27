# Record of Processing Activities (RoPA)

**Article 30 GDPR — record of processing activities.** This document is the
controller/processor record of the personal-data processing carried out by the
accounts-payable platform. It is a living document: update it whenever a
processing purpose, data category, recipient/sub-processor, retention window, or
transfer mechanism changes.

> **Roles.** For our own operational data (our employees/users, our auth records)
> we act as a **controller**. For the AP data our customers load into their
> tenants (their vendors, their invoices, their banking details, the expense
> data of *their* employees) we act as a **processor** on the customer's
> behalf — the customer is the controller. The Article 28 terms that govern that
> processor relationship are in
> [`docs/founder-runbooks/dpa-template.md`](founder-runbooks/dpa-template.md).

## Cross-references

| Topic | Document |
|---|---|
| Recipients / sub-processors (who else touches the data) | [`docs/sub-processors.md`](sub-processors.md) |
| Retention periods (how the windows are enforced) | [`backend/docs/retention.md`](../backend/docs/retention.md) |
| International transfers / data residency | [`docs/data-residency.md`](data-residency.md) |
| Breach notification procedure | [`docs/founder-runbooks/breach-notification.md`](founder-runbooks/breach-notification.md) |
| Cookie / non-essential consent | `frontend/src/lib/components/ConsentBanner.svelte` (banner) |

Retention windows below are summarized from
[`backend/docs/retention.md`](../backend/docs/retention.md); that document is the
authoritative source and the enforcement mechanism (the retention sweep,
`FEOH_RETENTION_*` env vars, the audit-log WORM store). Where a number here and
there diverge, the retention doc wins.

International-transfer notes below are summarized from
[`docs/data-residency.md`](data-residency.md); consult it for the per-region
hosting posture and the Standard Contractual Clauses (SCC) / UK IDTA / adequacy
basis that actually applies to a given customer.

---

## Processing activities

Each row is one processing activity (Art. 30(1)). "Legal basis" cites GDPR
Art. 6 where we are controller; where we are processor the customer (controller)
holds the legal basis and we process under the
[DPA](founder-runbooks/dpa-template.md) (Art. 28) on documented instructions.

### 1. Vendor / supplier PII

| Field | Detail |
|---|---|
| **Purpose** | Maintain the customer's vendor master; match invoices to vendors; pay suppliers; sanctions/KYC screening; 1099/tax reporting; supplier-portal access. |
| **Our role** | Processor (customer is controller). |
| **Legal basis** | Customer's: performance of a contract (Art. 6(1)(b)) with its supplier; legal obligation for tax/sanctions (Art. 6(1)(c)); legitimate interests for fraud prevention (Art. 6(1)(f)). We process on the customer's instructions per the DPA. |
| **Categories of data subjects** | Suppliers/vendors of the customer; vendor contacts; supplier-portal users (`VendorUser`). |
| **Categories of personal data** | Vendor/contact name, business email, phone, address; tax ID (EIN/SSN/VAT no.); bank account + routing/IBAN/sort-code (special-handling banking data); W-9/W-8 tax forms; sanctions-screening results. |
| **Recipients / sub-processors** | Sanctions/KYC providers, ERP integrations, payment rails, e-invoicing/PEPPOL access point — see [`docs/sub-processors.md`](sub-processors.md). |
| **Retention** | Tied to the customer's invoice/financial retention class (default 84 months); supplier-portal accounts purged on customer offboarding. See [`backend/docs/retention.md`](../backend/docs/retention.md). |
| **International transfers** | Per the customer's data-residency tier; SCCs/IDTA where applicable. See [`docs/data-residency.md`](data-residency.md). |

### 2. Employee / User PII (customer's AP staff and our staff)

| Field | Detail |
|---|---|
| **Purpose** | Provision and authenticate platform users; role/permission assignment; approval routing and segregation-of-duties; expense reimbursement; audit/non-repudiation of approvals. |
| **Our role** | Processor for the customer's users; controller for our own staff accounts. |
| **Legal basis** | Customer's: contract (Art. 6(1)(b)) / legitimate interests (Art. 6(1)(f)) for workforce administration. Ours: contract / legitimate interests for our own staff. |
| **Categories of data subjects** | Customer's AP clerks, managers, CFOs, admins; expense claimants (customer employees); our own personnel. |
| **Categories of personal data** | Name, work email, role, login identifiers, MFA enrolment metadata, IP/last-active for access reviews; expense-report claimant identity + receipts; approval actor identity on audit rows. |
| **Recipients / sub-processors** | SSO/SCIM identity providers (Okta/Entra/Authentik), email delivery — see [`docs/sub-processors.md`](sub-processors.md). |
| **Retention** | User records retained for the life of the customer account; audit rows are append-only WORM and retained per the audit-retention class. See [`backend/docs/retention.md`](../backend/docs/retention.md). |
| **International transfers** | Per the customer's data-residency tier; SCCs/IDTA where applicable. See [`docs/data-residency.md`](data-residency.md). |

### 3. Banking / payment-instrument data

| Field | Detail |
|---|---|
| **Purpose** | Execute payment runs (ACH/wire/check); issue and reconcile virtual cards; positive-pay fraud files; FX-locked international payments; sanctions screening before each payment. |
| **Our role** | Processor (customer is controller). |
| **Legal basis** | Customer's: contract (Art. 6(1)(b)); legal obligation (Art. 6(1)(c)); legitimate interests in fraud prevention (Art. 6(1)(f)). |
| **Categories of data subjects** | Suppliers being paid; virtual-card holders; bank-account holders. |
| **Categories of personal data** | Bank account + routing/IBAN/sort-code, account holder name; virtual-card PAN (single-use reveal only) + last-4; positive-pay account numbers. **Full account/PAN data is stored only in the object store / payment-rail, never in logs or error bodies** (project invariant). |
| **Recipients / sub-processors** | Payment rails (Modern Treasury, Stripe Treasury, Increase, Column, Dwolla), check printing, card issuers (Lithic/Nium) — see [`docs/sub-processors.md`](sub-processors.md). |
| **Retention** | Payment records tied to the financial-retention class (default 84 months for the tax/audit trail); card PAN never persisted server-side beyond the single-use reveal. See [`backend/docs/retention.md`](../backend/docs/retention.md). |
| **International transfers** | Cross-border where the payment rail or card issuer is non-EEA; SCCs/IDTA per provider. See [`docs/data-residency.md`](data-residency.md). |

### 4. Invoice / financial data

| Field | Detail |
|---|---|
| **Purpose** | Ingest, extract (AI/OCR), code, match (2/3/4-way), approve, and post invoices; expense management; contracts/CLM; analytics and CFO reporting; e-invoicing (PEPPOL); audit trail. |
| **Our role** | Processor (customer is controller). |
| **Legal basis** | Customer's: contract (Art. 6(1)(b)); legal obligation for tax/accounting records (Art. 6(1)(c)). |
| **Categories of data subjects** | Suppliers, supplier contacts, expense claimants, contract counterparties named on documents. |
| **Categories of personal data** | Names, contact details, and any personal data incidentally present on invoice/contract/receipt documents and line items; extraction results. |
| **Recipients / sub-processors** | AI extraction providers (when a customer enables a non-mock adapter), ERP integrations, e-invoicing/PEPPOL access point, object storage — see [`docs/sub-processors.md`](sub-processors.md). |
| **Retention** | Financial-records retention class, default 84 months; terminal invoices soft-archived by the retention sweep. See [`backend/docs/retention.md`](../backend/docs/retention.md). |
| **International transfers** | Per the customer's data-residency tier and any enabled AI/ERP provider; SCCs/IDTA where applicable. See [`docs/data-residency.md`](data-residency.md). |

### 5. Authentication / security data

| Field | Detail |
|---|---|
| **Purpose** | Authenticate users (JWT); MFA/TOTP; SSO (OIDC/SAML) and SCIM provisioning; session/token-blocklist management; sanctions/fraud controls; access reviews and SOX non-repudiation. |
| **Our role** | Processor for the customer's users; controller for our own security telemetry. |
| **Legal basis** | Customer's: contract (Art. 6(1)(b)); legitimate interests in account security (Art. 6(1)(f)). Ours: legal obligation/legitimate interests for security and SOX/SOC 2 controls. |
| **Categories of data subjects** | All platform users (customer staff, our staff) and supplier-portal users. |
| **Categories of personal data** | Login identifiers/email, hashed passwords (`bcrypt_sha256` — never plaintext), MFA secrets/enrolment state, JWT/session identifiers, IP and timestamps in audit rows, approval signatures. |
| **Recipients / sub-processors** | Identity providers (SSO/SCIM), Redis (token blocklist — self-hosted), centralized audit-log WORM sink (CloudWatch / S3 Object Lock) — see [`docs/sub-processors.md`](sub-processors.md). |
| **Retention** | Session/blocklist entries expire with the token; audit/security rows are append-only and retained per the audit-retention class. See [`backend/docs/retention.md`](../backend/docs/retention.md). |
| **International transfers** | Per the customer's data-residency tier and any SSO provider's region; SCCs/IDTA where applicable. See [`docs/data-residency.md`](data-residency.md). |

---

## Security measures (Art. 30(1)(g))

A general description — see `docs/soc2-readiness.md` and the project invariants
in the root `CLAUDE.md` for specifics:

- **Tenant isolation** at the data layer (database-per-tenant; header→`ap_<slug>`
  resolution cross-checked against the JWT `org` claim).
- **Encryption** in transit (TLS) and at rest (object store / DB); secrets via
  SOPS + AWS KMS, no hardcoded fallbacks.
- **Access control**: RBAC (`admin`/`ap_manager`/`ap_clerk`/`cfo`), MFA, SSO,
  periodic SOX access reviews flagging dormant elevated roles.
- **Auditability**: append-only audit log shipped to a WORM store
  (S3 Object Lock); approval signatures for non-repudiation.
- **PII/banking minimization in telemetry**: account/PAN/tax-ID data is kept out
  of logs and error responses by invariant.
- **Consent**: non-essential storage gated behind the consent banner
  (`frontend/src/lib/components/ConsentBanner.svelte`); essential JWT auth is
  exempt and disclosed as such.

## Maintenance

Review this record at least annually and on any material change to processing.
When you add an integration/provider, update both this RoPA and
[`docs/sub-processors.md`](sub-processors.md) in the same change.
