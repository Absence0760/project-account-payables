---
description: Verify the DSAR export reaches every personal-data field across BOTH database tiers plus object storage — GDPR Art. 15/20, CCPA right-to-know
---

Audit `POST /api/privacy/dsar` for completeness. Every field of personal data this platform holds about a subject must be reachable through it.

## Goal

Art. 15/20 and the CCPA right-to-know require a complete, machine-readable answer. The realistic failure here is **silent drift**: someone adds a model or a column, forgets the exporter, and months later a controller hands their data subject a partial archive — which is itself a compliance incident, and one *we* caused for *them*.

The structural trap in this codebase: personal data lives in **two database tiers plus object storage**, and an exporter that walks only one is incomplete by construction.

## What to check

1. **Read the walker.** `backend/app/services/privacy_export.py`, and `backend/docs/privacy.md` for the documented bundle shape per subject type (`user` / `vendor_user` / `vendor_contact`). List every table and column it pulls, per tier.
2. **Diff against the schema.** Enumerate `backend/app/models/*.py`. For each model, decide whether it can hold data about one of the three subject types, then check the exporter covers it. Be systematic — the ones that drift are the newest: `CashPlan`, `ReportDefinition`, `WorkflowExperiment`, `VendorStatementReconciliation`, `CorporateCardTransaction`, `ExpensePreapproval`, `DiscountOffer`, `SupplierChatMessage`, `DataSubjectRequest` itself.
3. **Both tiers.** Control plane: `User` (email, full_name, SSO subject ids, MFA metadata, notification prefs), `UserRole`, `ApiKey` (who minted it), `WebhookSubscription`, `WebAuthnCredential`, `ApiKeyUsage`. Tenant: `Vendor` contact fields, `VendorUser`, and everything authored by or about the subject. A subject-type that only ever reads one tier should say so explicitly in the doc — an unstated omission is indistinguishable from a bug.
4. **Object storage.** Uploaded bytes are personal data too. Check whether the bundle enumerates (or at minimum lists) the objects tied to the subject: invoice PDFs, expense receipts, contract documents, **W-9/W-8 tax forms** (`Vendor.w9_file_key`), vendor statements, supplier-chat attachments. If it exports keys rather than content, confirm the keys are actually resolvable by the controller — a key nobody can redeem is not portability.
5. **Free-text is where the surprises are.** `SupplierChatMessage` bodies, expense descriptions, invoice line-item descriptions and the `notes` fields can contain anything a human typed. Confirm they are in scope for the subjects who authored them.
6. **Derived and inferred data.** Vendor risk scores, sanctions screening results and their `categories`, adaptive-workflow approver statistics, `overturn_rate_pct` — Art. 15 covers inferences, not just what the subject supplied. Check whether they are exported and, if deliberately excluded, whether that is documented.
7. **Redacted-vs-absent.** `Vendor.bank_details` and `tax_id` are personal/financial data the subject is entitled to. Confirm what the bundle returns and that the doc matches: exporting a masked form is a defensible choice, exporting nothing silently is not.
8. **Format + fidelity.** Machine-readable JSON (Art. 20). Money must serialise as an **exact decimal string**, never a float — that is a project invariant and a correctness bug in an export the controller may reconcile against.
9. **Scale.** A vendor with 50 000 invoices: does the bundle assemble synchronously without timing out or exhausting memory? An export that only works for small accounts is **Medium** — the right fix is streaming or pagination, not a bigger timeout.
10. **The audit row.** Confirm the export itself writes `privacy.dsar_export` and that the row records who asked and for whom without embedding the exported PII.

## Report

Per gap: the model + field (or storage prefix) missed, which subject type it affects, which tier it lives in, and the exact addition to `privacy_export.py`. Severity per the `compliance-auditor` rubric — a missed store is **High** (a right the DPA promises that the code cannot deliver).

Finish with the **clean** list of models you confirmed are either covered or legitimately out of scope, so the next run can diff against it.

## Delegate to

Use the `compliance-auditor` agent: `"Audit data-export completeness — diff privacy_export.py against every model in both database tiers plus object storage, and check format, inferences, free text, and scale."`

Read-only. Findings only.
