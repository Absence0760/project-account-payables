# .claude/

Project-scoped sub-agents and slash commands. Checked into git so every contributor (and every future Claude session) gets the same review surface.

## Sub-agents

Specialised review-only agents invoked by the slash commands or by name from any conversation. All four are read-only — they report findings, they don't edit.

| Agent | What it does |
|---|---|
| [`agents/code-reviewer.md`](agents/code-reviewer.md) | Reviews the working diff against the project's documented conventions (root `CLAUDE.md`, ADRs, money-path / tenant-isolation / auth / secrets invariants). Outputs `CLEAN` or `NEEDS_CHANGES` with concrete file:line findings. |
| [`agents/test-gap-checker.md`](agents/test-gap-checker.md) | Cross-references modified source files against the test files in the diff. Reports which unit / integration / e2e tests the change should ship with. |
| [`agents/doc-hygiene-checker.md`](agents/doc-hygiene-checker.md) | Walks the doc set (README, `docs/*`, `CLAUDE.md` files) and reports which docs the diff invalidated. |
| [`agents/repo-security-auditor.md`](agents/repo-security-auditor.md) | Generic security sweep across the five trust boundaries — tenant isolation, auth, money path, secrets, PII. Pass the audit area as the prompt's first sentence. |

## Slash commands

| Command | What it does |
|---|---|
| [`commands/check.md`](commands/check.md) | Pre-commit gate. Spawns `code-reviewer` + `test-gap-checker` + `doc-hygiene-checker` in parallel against the working diff and aggregates findings. Advisory only — does not apply fixes or commit. Use before every non-trivial commit. |
| [`commands/safe-edit.md`](commands/safe-edit.md) | Implements `<task>` with a coder ↔ reviewer loop (max 2 review cycles). Costs 2-3x a normal edit. Use for money-path / tenant-isolation / auth / migration / webhook changes. |

## What lives here vs. what doesn't

- **In `.claude/`**: agent and command definitions that are useful to every contributor and every Claude session against this repo.
- **Not in `.claude/`**: settings.local.json (per-user), runtime locks (`*.lock`), or anything user-specific. The repo `.gitignore` keeps those out.

## Adapting these as the project grows

The agents currently cite generic financial-system invariants (money is exact, idempotency on payment moves, tenant isolation, audit trail, secrets via sops + KMS). As the project lands on a specific stack, edit the agents to:

- Cite the actual file paths the project uses for the auth middleware, the tenant-scoping helper, the migration directory, the test directory, etc.
- Replace generic "money is exact" rules with the concrete library / Money type / column type the project chose.
- Add project-specific invariants from `docs/decisions.md` ADRs as they get written.

The pattern is "keep the framework, swap in the specifics." Don't rewrite from scratch each time the stack shifts.
