# Data Processing Agreement (DPA) — TEMPLATE

> **⚠️ THIS IS A TEMPLATE, NOT LEGAL ADVICE.** It is a starting point for a
> customer-facing Data Processing Agreement (GDPR Article 28 processor terms).
> It has **not** been reviewed by counsel. Do not send it to a customer, sign
> it, or rely on it until a qualified data-protection lawyer has reviewed and
> adapted it to your actual operations, sub-processor list, and target
> jurisdictions. Bracketed `[…]` fields must be completed. Where this template
> and a customer's negotiated terms conflict, the executed agreement governs.

---

## Data Processing Agreement

This Data Processing Agreement ("**DPA**") forms part of the agreement for
services (the "**Agreement**") between **[Customer legal name]** ("**Controller**")
and **[Your company legal name]** ("**Processor**") and governs the Processor's
processing of Personal Data on the Controller's behalf in connection with the
accounts-payable platform (the "**Service**").

Where the Controller is itself a processor for a third party, this DPA applies
on a back-to-back basis and the Processor acts as a sub-processor.

### 1. Definitions

"**Personal Data**", "**processing**", "**controller**", "**processor**",
"**data subject**", "**personal data breach**", and "**supervisory authority**"
have the meanings given in the **GDPR** (Regulation (EU) 2016/679) and, where
applicable, the **UK GDPR** and **Data Protection Act 2018**. "**Data Protection
Laws**" means all privacy and data-protection laws applicable to the processing,
including the GDPR, UK GDPR, and the **CCPA/CPRA** where the Controller's data
includes California consumers' personal information. Other capitalized terms have
the meaning given in the Agreement.

### 2. Subject-matter, duration, nature and purpose

- **Subject-matter & nature**: hosting and processing of accounts-payable data
  — vendor records, invoices, expenses, contracts, payment instructions, and the
  authentication of the Controller's users — to provide the Service.
- **Purpose**: solely to provide, maintain, secure, and support the Service in
  accordance with the Controller's documented instructions (the Agreement, this
  DPA, and the Controller's use of the Service's configuration controls).
- **Duration**: for the term of the Agreement and until deletion/return under
  Section 11.
- **Types of Personal Data and categories of data subjects**: as described in
  **Annex I** below and in the Processor's Record of Processing Activities,
  [`docs/ropa.md`](../ropa.md).

### 3. Processor obligations

The Processor shall:

1. process Personal Data **only on the Controller's documented instructions**,
   including with regard to international transfers, unless required by law (in
   which case it will notify the Controller unless legally prohibited);
2. ensure persons authorized to process Personal Data are bound by
   **confidentiality**;
3. implement appropriate **technical and organizational measures** (Article 32),
   as summarized in **Annex II**;
4. respect the conditions in Section 5 for engaging **sub-processors**;
5. **assist** the Controller (Section 6) in responding to data-subject requests;
6. assist the Controller with security, breach notification, DPIAs, and prior
   consultations (Articles 32–36);
7. at the Controller's election, **delete or return** Personal Data at the end of
   the engagement (Section 11);
8. make available information necessary to demonstrate compliance and allow for
   and contribute to **audits** (Section 9);
9. immediately inform the Controller if, in its opinion, an instruction infringes
   Data Protection Laws.

### 4. Controller obligations

The Controller warrants that it has a lawful basis for the processing, that its
instructions are lawful, and that it has provided any notices and obtained any
consents required for the Processor to process the Personal Data.

### 5. Sub-processors

1. The Controller provides **general authorization** for the Processor to engage
   sub-processors. The current list is maintained at
   [`docs/sub-processors.md`](../sub-processors.md).
2. The Processor will give the Controller **[30] days' prior notice** of any
   intended addition or replacement of a sub-processor (by updating the register
   and/or notifying the Controller), giving the Controller the opportunity to
   **object** on reasonable data-protection grounds.
3. The Processor will impose **data-protection obligations no less protective**
   than this DPA on each sub-processor by written contract and remains **liable**
   for its sub-processors' performance.

### 6. Assistance with data-subject rights

Taking into account the nature of the processing, the Processor shall assist the
Controller by appropriate technical and organizational measures, insofar as
possible, to respond to data-subject requests to exercise rights of access,
rectification, erasure, restriction, portability, and objection. Where a data
subject contacts the Processor directly, the Processor will, where permitted,
forward the request to the Controller and not respond except on the Controller's
instruction.

### 7. Personal data breach

The Processor shall notify the Controller **without undue delay** (and in any
event within **[48–72] hours**) after becoming aware of a personal data breach
affecting the Controller's Personal Data, and shall provide the information the
Controller reasonably needs to meet its own notification obligations
(Articles 33–34). The Processor's internal breach-handling procedure is in
[`docs/founder-runbooks/breach-notification.md`](breach-notification.md).

### 8. International transfers

1. The Processor shall not transfer Personal Data outside the EEA/UK except in
   accordance with the Controller's instructions and a valid transfer mechanism.
2. Where the Processor or a sub-processor processes Personal Data in a country
   without an **adequacy decision**, the parties incorporate the **EU Standard
   Contractual Clauses (SCCs)** (Module appropriate to the relationship) and,
   for UK data, the **UK International Data Transfer Addendum (IDTA)** / UK
   Addendum to the SCCs, completed as set out in **Annex III**.
3. The Processor's regional hosting posture and the transfer basis that applies
   per region are described in [`docs/data-residency.md`](../data-residency.md).

### 9. Audit rights

The Processor shall make available to the Controller information necessary to
demonstrate compliance with Article 28 and allow for and contribute to audits,
including inspections, conducted by the Controller or an auditor it mandates,
**[once per 12-month period, on reasonable prior notice, during business hours,
subject to confidentiality]**. The Processor may satisfy audit requests by
providing its current **SOC 2** report and security documentation (see
`docs/soc2-readiness.md`) where this reasonably meets the Controller's needs.

### 10. CCPA/CPRA terms

To the extent the Controller's Personal Data includes the personal information of
California residents, the Processor acts as a **service provider** and shall not:
(a) sell or share the personal information; (b) retain, use, or disclose it for
any purpose other than performing the Service or as permitted by the CCPA/CPRA;
or (c) combine it with personal information from other sources except as
permitted. The Processor certifies it understands and will comply with these
restrictions.

### 11. Deletion or return on termination

On expiry or termination of the Agreement, and at the Controller's choice, the
Processor shall **delete or return** all Personal Data and delete existing
copies, unless retention is required by law. Deletion windows and the
append-only audit-log retention (which is retained for compliance and cannot be
deleted on request) follow [`backend/docs/retention.md`](../../backend/docs/retention.md).

### 12. Liability and precedence

Liability under this DPA is subject to the limitations in the Agreement. In the
event of conflict between this DPA and the Agreement on the subject of personal
data, **this DPA prevails**; the SCCs prevail over both on the subject of
restricted transfers.

---

## Annex I — Details of processing

| Item | Detail |
|---|---|
| **Categories of data subjects** | Controller's vendors/suppliers and their contacts; Controller's employees/users (AP staff, approvers, expense claimants); supplier-portal users; contract counterparties. |
| **Categories of personal data** | Names, business contact details, role/login identifiers; tax IDs; bank account / routing / IBAN / sort-code; payment-instrument data; invoice/contract/receipt content; authentication and security metadata. See [`docs/ropa.md`](../ropa.md) for the full mapping. |
| **Special categories** | None intended. The Controller shall not load special-category data into the Service except where expressly agreed. |
| **Frequency** | Continuous, for the term. |
| **Nature & purpose** | As in Section 2. |
| **Retention** | As in [`backend/docs/retention.md`](../../backend/docs/retention.md). |

## Annex II — Technical and organizational measures

Summarized; see `docs/soc2-readiness.md` and [`docs/ropa.md`](../ropa.md)
§ Security measures for specifics: tenant isolation at the data layer;
encryption in transit and at rest; secrets in SOPS + AWS KMS (no hardcoded
fallbacks); RBAC + MFA + SSO; periodic access reviews; append-only audit log
shipped to a WORM store; PII/banking data kept out of logs and error responses;
non-essential storage gated behind a consent banner.

## Annex III — Sub-processors and transfer mechanisms

The current sub-processor list and each processor's location / transfer basis is
maintained in [`docs/sub-processors.md`](../sub-processors.md). The SCC modules,
clause selections, and any UK IDTA completions are recorded there or in the
executed transfer documentation.

---

### Signatures

| | Controller | Processor |
|---|---|---|
| Name | `[…]` | `[…]` |
| Title | `[…]` | `[…]` |
| Date | `[…]` | `[…]` |
| Signature | `[…]` | `[…]` |

> **Reminder:** counsel must review before use. See the warning at the top.
