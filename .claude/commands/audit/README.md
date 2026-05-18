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

### Per-domain audits

| Command | What it checks |
|---|---|
| [/audit/auth](auth.md) | Auth middleware gating + tenant-context wrapper discipline across every route |

### Dispatcher

| Command | What it does |
|---|---|
| [/audit/all](all.md) | Spawns the full sweep in parallel + consolidated report. |

## Conventions

- Every audit is **read-only by default**. The deliverable is a findings report, not a diff.
- Findings are grouped by severity: **Critical / High / Medium / Low**.
- Each command is a **self-contained prompt** — runnable from a fresh session with no prior context.
- Cross-references: findings tie back to the project invariants in the root `CLAUDE.md` and the per-area `CLAUDE.md` rules they violate.

## Agent delegation

All audits delegate to the `repo-security-auditor` agent (under `.claude/agents/`). That agent has the project's trust boundaries baked in (auth, tenant isolation, money path, secrets, PII) — it picks up the project's conventions without re-reading them every run.

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
- **Periodically (monthly)** — `/audit/all` to catch slow-moving drift.

## Targeted vs full sweep

For narrow changes (just a webhook handler, just one route), prefer the targeted command directly rather than `/audit/all` — the full sweep is parallel but still slow because each sub-audit walks a large surface area.
