# Audit commands

Project-curated slash commands for running security audits across the repo. Each is read-only by default — they report findings, they don't apply fixes without explicit confirmation.

Invoke from a Claude Code session as `/audit/<name>` for the commands in this directory, or as `/audit-<name>` for the top-level security audits.

## Index

### Top-level security audits

These live at the repo root (one directory up):

| Command | What it checks |
|---|---|
| [/audit-security](../audit-security.md) | Auth, tenant isolation, money path, secrets, PII boundaries |
| [/audit-webhooks](../audit-webhooks.md) | HMAC verification, event-id dedupe, fail-closed responses |
| [/audit-money-path](../audit-money-path.md) | `Decimal` vs `float`, idempotency, audit-trail rows on money-moving writes |

### Per-domain security audits

| Command | What it checks |
|---|---|
| [/audit/auth](auth.md) | Auth middleware gating + tenant-context wrapper discipline across every route |
| [/audit/secrets](secrets.md) | Secrets on the wrong side of the client/server or public-repo boundary; git history; workflow credentials |
| [/audit/xss](xss.md) | Untrusted text becoming markup, CSS, a CSV cell or an email body |
| [/audit/deps](deps.md) | Four ecosystems (pnpm, uv/pip, pub, Terraform) + Dependabot coverage + action SHA pinning |
| [/audit/infra](infra.md) | `infra/` Terraform + the AWS deploy pipeline — KMS, Object Lock, OIDC least-privilege |
| [/audit/llm-endpoint](llm-endpoint.md) | Every LLM surface — cost ceilings, prompt injection from supplier documents, tool scoping, PII in prompts |

### Compliance audits

These delegate to the `compliance-auditor` agent rather than
`repo-security-auditor` — it carries the personal-data map, the DSAR paths and
the sub-processor register.

| Command | What it checks |
|---|---|
| [/audit/gdpr](gdpr.md) | Processor duties against the DPA, lawful basis, retention, transfers, breach readiness |
| [/audit/data-export-completeness](data-export-completeness.md) | Does the DSAR export reach every field, both DB tiers, and object storage? |
| [/audit/account-deletion-completeness](account-deletion-completeness.md) | Does erasure clear every store, Redis key and third-party copy — and is what survives genuinely non-PII? |
| [/audit/third-party-data-flows](third-party-data-flows.md) | Drift between the code's outbound hops and `docs/sub-processors.md` |
| [/audit/cookie-consent](cookie-consent.md) | Does the banner gate what it claims, and does anything load before consent? |
| [/audit/regional-availability](regional-availability.md) | Jurisdictions we claim to serve vs. what tax, e-invoicing and payment rails actually support |
| [/audit/accessibility](accessibility.md) | WCAG 2.2 AA / EAA / ADA across the web and Flutter surfaces |

### Dispatcher

| Command | What it does |
|---|---|
| [/audit/all](all.md) | Spawns the full sweep in parallel + consolidated report. |

### Fix-side counterparts

The audits above are read-only reporters. Where a fix loop exists, hand the
findings to it rather than patching inside the audit:

| Reporter | Fix loop |
|---|---|
| `/audit/accessibility` | [/a11y-hunt](../a11y-hunt.md) — computes each claim, fixes at the shared token, lands the guard in the same commit |
| any correctness finding | [/bug-hunt](../bug-hunt.md), [/audit-and-fix](../audit-and-fix.md) |

## Conventions

- Every audit is **read-only by default**. The deliverable is a findings report, not a diff.
- Findings are grouped by severity: **Critical / High / Medium / Low**.
- Each command is a **self-contained prompt** — runnable from a fresh session with no prior context.
- Cross-references: findings tie back to the project invariants in the root `CLAUDE.md` and the per-area `CLAUDE.md` rules they violate.

## Agent delegation

Each command's `## Delegate to` section names its agent — don't guess:

- **`repo-security-auditor`** for the security family. It carries the project's trust boundaries (auth, tenant isolation, money path, secrets, PII).
- **`compliance-auditor`** for the compliance family. It carries the personal-data map across both database tiers, the DSAR export/erasure paths, the local-first adapter posture, and the SOX/WORM-vs-erasure tension.
- **`general-purpose`** for `/audit/deps`, which is mostly running tools and reading output.

Both auditor agents are written against *this* repo. If one starts describing a stack that isn't here, it has been overwritten by a scaffolding sync — fix the agent, don't work around it.

`/audit/all` spawns one agent per area in parallel.

## Diff-time enforcement (complementary)

For per-PR enforcement (as opposed to periodic broad sweeps), use:

- [/check](../check.md) — pre-commit gate: `code-reviewer` + `test-gap-checker` + `doc-hygiene-checker` in parallel against the working diff.
- [/safe-edit](../safe-edit.md) — coder ↔ reviewer loop for non-trivial changes (~2-3x cost; use for security-sensitive or money-path changes).

These are for per-PR / pre-deploy enforcement; the audit commands here are for periodic broad sweeps.

## When to run

- **Before a release** — `/audit/all` once, fix Critical / High before tagging.
- **After bumping a dependency major** — `/audit-security` (catches new auth/secret surfaces).
- **After adding a new backend route or webhook** — `/audit-security` + `/audit-webhooks` + `/audit/auth`.
- **After touching the invoice → payment → ERP path** — `/audit-money-path`.
- **After adding a model, a column, or a new store** — `/audit/data-export-completeness` + `/audit/account-deletion-completeness`. Those two drift the moment the schema does.
- **After configuring a new external provider** — `/audit/third-party-data-flows` (the register) + `/audit/gdpr` (the transfer mechanism).
- **After touching an LLM prompt, tool or adapter** — `/audit/llm-endpoint`.
- **Before onboarding a customer outside the US** — `/audit/gdpr` + `/audit/regional-availability`.
- **Periodically (monthly)** — `/audit/all` to catch slow-moving drift.

## Targeted vs full sweep

For narrow changes (just a webhook handler, just one route), prefer the targeted command directly rather than `/audit/all` — the full sweep is parallel but still slow because each sub-audit walks a large surface area.
