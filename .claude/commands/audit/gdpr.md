---
description: Audit the GDPR / UK GDPR posture — controller-vs-processor duties, lawful basis, DSAR machinery, retention, transfers, Art. 28 flow-down, breach readiness
---

Audit this platform's GDPR / UK GDPR posture. The deliverable is a punch list the user can fix before onboarding an EU/UK customer.

## Goal

Get the role right first, because it decides who owes what. **Our customer (the tenant org) is the controller**; we are their **processor**; every external adapter a tenant switches on is a **sub-processor**. Almost nothing here is B2C — the data subjects are the customer's staff, their suppliers' portal logins, and their suppliers' contact details. So the failure mode is not "we lack a consent banner for our users"; it is "we promised a controller something in the DPA that our code cannot deliver."

## What to check

1. **Art. 28 processor duties, against `docs/founder-runbooks/dpa-template.md`.** For every commitment the template makes — assist with data-subject requests, notify breaches without undue delay, sub-processor notice and objection rights, deletion/return at termination, audit rights — is there code or a runbook that actually performs it? A promise with no mechanism is **High**.
2. **Lawful basis per data category.** Walk `docs/ropa.md`. Most processing is Art. 6(1)(b)/(f) on the controller's instructions. Check the edges specifically: sanctions/PEP/adverse-media screening of **named individuals** (beneficial owners) is profiling with a legal-obligation flavour and deserves its own basis; AI extraction ships a supplier's document to a third-party model; enrichment sends vendor identifiers to D&B/Clearbit. Flag any category in the ROPA with no basis named, and any processing in the code with no ROPA row.
3. **DSAR machinery.** `backend/docs/privacy.md` + `backend/app/api/privacy.py`. Confirm: export and erasure both reach **both** database tiers; both are audited; erasure is idempotent; the `DataSubjectRequest` row gives the controller the record they need to answer their own regulator. Depth belongs to `/audit/data-export-completeness` and `/audit/account-deletion-completeness` — here, just confirm the machinery exists and is admin-gated.
4. **The erasure-vs-immutability tension is documented, not just handled.** `audit_log` is append-only and shipped to S3 Object Lock; those rows **cannot** be erased, by design and by bucket policy. That is defensible (Art. 17(3)(b) legal obligation, SOX), but only if it is written down for the controller. If the DPA and privacy doc do not state what survives erasure and why, that is **High** — it is the finding a DPO will raise.
5. **Retention.** `backend/docs/retention.md`. Windows are per-tenant config and the sweep is **off by default** (`FEOH_RETENTION_ENABLED`). Storage-limitation (Art. 5(1)(e)) is not satisfied by a configurable knob nobody turns: check whether there is a default window per record class, whether an operator is told to enable the sweep, and what happens to object-storage files and backups when a row ages out.
6. **Transfers.** For each **configured** adapter (not the whole register — see the local-first note in the `compliance-auditor` agent), is the processing region recorded in `docs/sub-processors.md`, and is there a transfer mechanism (SCCs / UK IDTA / adequacy) named for an EU tenant using a US-hosted provider? A latent, unconfigured adapter is a **pre-activation blocker**, not a live violation — say which it is.
7. **Residency claims.** `docs/data-residency.md`. The region pin is advisory: nothing routes on it and no data moves. Confirm the product surface says so plainly — a customer reading "Data Residency: EU" in the UI and inferring their data is stored in the EU is the misrepresentation risk. The `alignment: null` on an unknown deployed region is the correct fail-safe; confirm nothing has "helpfully" defaulted it to `true`.
8. **Breach readiness.** `docs/founder-runbooks/breach-notification.md` — is there a named owner, a 72-hour clock, a controller-notification path (we notify our customers; they notify regulators), and a way to determine scope? A runbook that cannot answer "which tenants were affected" is **Medium** at best.
9. **Art. 27 EU representative / DPO.** Needed once EU personal data is processed at scale from outside the EU. Flag as a business action, not a code finding.
10. **Special categories + children.** Neither should be present — this is B2B AP data. If you find a data path that could carry Art. 9 data (a free-text supplier-chat message, an expense receipt, an uploaded document), note that the *container* is uncontrolled even though the schema is clean.

## Report

Use the severity rubric in the `compliance-auditor` agent. For each finding, name the article and say whether the fix is code, doc, or a business decision — mixing those three is what makes GDPR punch lists stall.

End with the **clean** list.

## Delegate to

Use the `compliance-auditor` agent: `"Audit GDPR posture — processor duties against the DPA template, lawful basis per ROPA category, DSAR machinery, the erasure-vs-WORM tension, retention defaults, transfer mechanisms for configured adapters, residency claims, and breach readiness."`

Read-only. Findings only.
