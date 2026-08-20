---
description: Verify erasure clears every personal-data field, Redis key, stored object and third-party copy — GDPR Art. 17 — while correctly preserving the immutable money trail
---

Audit `POST /api/privacy/erasure` for completeness. After it runs, nothing personal about the subject should remain — **except** what a legal obligation requires us to keep, and that exception must be deliberate, minimal and documented.

## Goal

This is the mirror of `/audit/data-export-completeness` and shares its map, but it has an extra dimension the export does not: erasure must also reach data **outside the primary database** — Redis, object storage, and third parties we already handed a copy to.

It also has a hard boundary the export does not. The `audit_log` is append-only (DB trigger) and shipped to S3 Object Lock; **those rows cannot be deleted and must not be**. So "did we delete everything?" is the wrong question. The right one is: *is everything that survives genuinely non-PII and genuinely required?*

## What to check

1. **Read the eraser.** `backend/app/services/privacy_erasure.py` + `backend/docs/privacy.md`. Map each redaction to a table and column; note what it nulls, what it tombstones, and what it rewrites to a placeholder.
2. **Diff against the schema**, exactly as `/audit/data-export-completeness` does — the two walkers must cover the same surface. A field the exporter returns but the eraser does not clear is a guaranteed finding; **run that diff explicitly**, it is the highest-yield check here.
3. **Free text is the classic miss.** Redacting `Vendor.email` while leaving the supplier's name and address quoted inside a `SupplierChatMessage` body, an invoice `notes` field, or an expense description erases nothing in practice. Check whether free-text columns are in scope and, if not, whether that limitation is stated.
4. **Redis.** Erasure must invalidate live access, not just rows: JWT blocklist entries for the subject's sessions, pending MFA enrollment secrets (`mfa:pending_enroll:`, `mfa:vendor_pending_enroll:`), WebAuthn challenges, rate-limit keys derived from a hashed identifier. A subject whose row is redacted but whose JWT still works is **High**.
5. **Credentials and second factors.** `User.mfa_secret`, every `WebAuthnCredential`, `VendorUser` password hash and TOTP columns, API keys the subject minted (revoke, don't just orphan), and any active session cap entry.
6. **Object storage.** Every object keyed to the subject: W-9/W-8 tax forms, uploaded invoices and receipts they authored, chat attachments, and any generated DSAR bundle from an **earlier** export — that bundle is a full PII archive sitting in a bucket, and it is the one people forget. Confirm the prefix walker actually enumerates rather than assuming a flat layout.
7. **Third-party copies.** We are not the only holder. Check whether erasure revokes or requests deletion at each **configured** sub-processor that received the subject's data: an issued virtual card at Lithic/Nium, a vendor record pushed to the ERP, a sanctions-screening record at the provider, a Stripe billing customer, an outbound webhook target that received the subject's identifiers, chat-notification posts already delivered to Slack/Teams. Where deletion is impossible, the finding is that the DPA must say so.
8. **What survives, and whether it should.** Enumerate every row that deliberately persists — `audit_log` and its WORM copy, `Payment`/`Invoice` money rows, the `DataSubjectRequest` record of the erasure itself. For each: confirm what remains is an actor id, an action and an entity id rather than a name, email or bank detail. **Do not assume** — read an actual audit row's `details` JSONB shape. A name that leaked into an audit `details` payload is now in an undeletable WORM store, which is **Critical**.
9. **Idempotency and ordering.** A re-run returns `noop`, not an error and not a second partial pass. Ordering must not strand a child row whose parent is gone before it is redacted. Cross-DB: the tenant leg commits before the control-plane leg (a control-plane user deleted first leaves the tenant leg unable to resolve the subject).
10. **Backups.** Restoring a pre-erasure backup silently resurrects the subject. Check `docs/backup-disaster-recovery.md` for a re-application procedure or a documented retention window; absence is **Medium**, and it is a question every DPO asks.

## Report

Per gap: the store (table / Redis key / bucket prefix / third party), what survives, the subject type affected, and the exact change. Distinguish **PII that survives and should not** (High/Critical) from **PII that survives by legal obligation but is undocumented** (High, doc fix) from **a store the eraser cannot reach at all** (design finding).

Finish with the **clean** list.

## Delegate to

Use the `compliance-auditor` agent: `"Audit erasure completeness — diff privacy_erasure.py against privacy_export.py and the full schema, then check Redis, credentials, object storage, third-party copies, what deliberately survives in the WORM audit trail, idempotency/ordering, and backups."`

Read-only. Findings only.
