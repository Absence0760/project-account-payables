---
description: Re-derive every outbound personal/financial-data hop from the code and diff it against docs/sub-processors.md — the Art. 28/30 sub-processor register drift check
---

Audit every outbound flow of personal or financial data to a third party, and reconcile it against the register.

## Goal

`docs/sub-processors.md` already exists and is good — it lists each adapter, its data categories, its processing region and its DPA status, and it feeds the customer DPA's sub-processor schedule, `docs/ropa.md`, and the SOC 2 vendor-management module. **So your job is drift, not rediscovery.** A register that was accurate six months ago and silently isn't now is worse than no register: it is a written statement to customers that has become false.

Keep the local-first posture in view (see the `compliance-auditor` agent): a default install shares data with no external sub-processor, and every adapter fails closed without a credential. An unconfigured adapter is a **pre-activation blocker**, not a live transfer — label each finding accordingly.

## What to check

1. **Enumerate the adapter families from the filesystem, not from memory:** `ls -d backend/app/services/*_adapters/ backend/app/services/audit_shipping/`. For each directory, list its registered providers (grep the `@register_*` decorators).
2. **Diff that list against the register's sections.** Every provider needs a row; every row needs a provider. The register covered only the first fifteen adapter families until billing, chat-notification, enrichment and QMS were added — that is the shape of the drift to expect, so the check that matters is *which family was added to the code most recently, and does it have a row*. A newly-added family with no section is **High** if it is configured anywhere, **Medium** if it is still latent.
3. **Verify each existing row's data categories against what the adapter actually sends.** Read the payload the adapter builds — not its docstring. The categories drift when a field is added: a chat-notification post that gains a line-item description has moved from PII-free to COMMS.
4. **Find outbound calls that escape the adapter pattern entirely.** Grep `backend/app/` for `httpx.AsyncClient`, `client.post(`, `client.get(`, `requests.`, `boto3.client(` and any hardcoded `https://` host, then subtract everything already accounted for. A direct call from a service or route — bypassing the registry — is both a register gap and an architectural finding.
5. **Check the outbound-webhook surface separately.** `/api/webhooks` lets a **customer** nominate an arbitrary target URL that then receives event payloads. That is a customer-directed transfer rather than a sub-processor of ours, and it should be described as such (and its SSRF guard, `FEOH_WEBHOOKS_ALLOW_PRIVATE_TARGETS`, is a security control worth confirming is still default-off). Same for scheduled reports, which email a CSV of tenant AP spend to arbitrary addresses.
6. **Region + DPA columns.** Any row still reading "to be confirmed" for a provider that is now actually configured in a deployed environment is **High** — that is an active transfer with no recorded flow-down. Check `docs/data-residency.md` for a US-hosted provider serving an EU-pinned tenant.
7. **The infra rows.** AWS is active in any deployed environment by design — confirm the services listed (S3, CloudWatch Logs, KMS, SES if enabled) match what `infra/` and the code actually use.
8. **Egress that isn't an API call.** Outbound email (`email_adapters`) carries whatever the notification body holds; the audit-log shipper streams `audit_log` rows to CloudWatch and S3; PEPPOL transmits a full structured invoice onto a public network via an Access Point. All three are transfers even though none looks like a vendor integration.

## Report

The deliverable is a **corrected register table** — same columns as `docs/sub-processors.md` (Adapter | Processor | Service | Data categories | Processing location | Active when configured | DPA status) — with additions, corrections and deletions marked, ready to paste in. Precede it with the findings list:

- **High** — an active transfer with no register row, or a row whose data categories understate what is sent.
- **Medium** — a latent adapter with no row (pre-activation blocker), a stale "to be confirmed" on a configured provider.
- **Low** — wording drift, a region that has since been narrowed.

Note the `## Maintenance` section at the foot of `docs/sub-processors.md` — if this audit produces corrections, say so there too.

## Delegate to

Use the `compliance-auditor` agent: `"Audit third-party data flows — enumerate every adapter family and outbound HTTP call from the code, diff against docs/sub-processors.md, and produce the corrected register table."`

Read-only. Report the corrected table; don't edit the register without confirmation.
