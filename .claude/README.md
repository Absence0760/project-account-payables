# .claude/

Project-scoped sub-agents, slash commands, and hooks. Checked into git so every contributor (and every future Claude session) gets the same review surface.

## Sub-agents

Specialised review-only agents invoked by the slash commands or by name from any conversation. All four are read-only — they report findings, they don't edit.

| Agent | What it does |
|---|---|
| [`agents/code-reviewer.md`](agents/code-reviewer.md) | Reviews the working diff against the project's documented conventions (root `CLAUDE.md`, ADRs, money-path / tenant-isolation / auth / secrets invariants). Outputs `CLEAN` or `NEEDS_CHANGES` with concrete file:line findings. |
| [`agents/test-gap-checker.md`](agents/test-gap-checker.md) | Cross-references modified source files against the test files in the diff. Reports which unit / integration / e2e tests the change should ship with. |
| [`agents/doc-hygiene-checker.md`](agents/doc-hygiene-checker.md) | Walks the doc set (README, `docs/*`, `CLAUDE.md` files) and reports which docs the diff invalidated. |
| [`agents/repo-security-auditor.md`](agents/repo-security-auditor.md) | Security sweep across the five trust boundaries — tenant isolation, auth, money path, secrets, PII. The "Known bug shapes" section encodes every regression that's shipped to a branch so the agent learns from history. Pass the audit area as the prompt's first sentence. |

## Slash commands

| Command | What it does |
|---|---|
| [`commands/check.md`](commands/check.md) | Pre-commit gate. Spawns `code-reviewer` + `test-gap-checker` + `doc-hygiene-checker` in parallel against the working diff and aggregates findings. Advisory only. Use before every non-trivial commit. |
| [`commands/safe-edit.md`](commands/safe-edit.md) | Implements `<task>` with a coder ↔ reviewer loop (max 2 review cycles). Costs 2-3x a normal edit. Use for money-path / tenant-isolation / auth / migration / webhook changes. |
| [`commands/audit-security.md`](commands/audit-security.md) | On-demand security audit. Invokes `repo-security-auditor` against a focus area (tenant isolation, money path, webhooks, secrets, PII, migrations, infra). Heavier than `/check`. |
| [`commands/audit-webhooks.md`](commands/audit-webhooks.md) | Focused audit of every inbound webhook handler against invariant #9 (HMAC verification + event dedup + silent rejection). |
| [`commands/audit-money-path.md`](commands/audit-money-path.md) | Focused audit of every money-moving path against invariants #1 (Decimal/Numeric), #2 (idempotency), #3 (append-only audit trail). |

## Hooks

| Hook | When it runs | What it does |
|---|---|---|
| [`hooks/security-patterns.sh`](hooks/security-patterns.sh) | PostToolUse on `Edit` / `Write` / `MultiEdit` | Grep-based pattern checks for security regressions. Catches the textually-stable bug classes (bcrypt scheme, naive datetime, raw filename interpolation, exception-in-log, jwt.decode outside the central helper, direct status assignment, Float on money column, secret-shaped response fields, raw fetch in Svelte components, console.log in committed source, TODO without owner). Each rule names the bug class it prevents and the safer alternative. Bypass with `# noqa: <rule-name>` on the line with a rationale. |

Wired in [`settings.json`](settings.json). The hook exits 2 on a finding so stderr surfaces as a system-reminder for the next turn.

## Where to reach in which order

| Layer | What it catches | Cost |
|---|---|---|
| `hooks/security-patterns.sh` | Stable textual shapes — caught on every Edit, before the next turn | ~50ms per edit |
| `/check` | Diff-level review against documented conventions | ~30s |
| `/audit-security` | Trust-boundary sweep across the area named | ~1–2min |
| `/audit-webhooks` | Four-question gate on every webhook handler | ~1min |
| `/audit-money-path` | Three-invariant gate on every money-moving path | ~1min |
| `/safe-edit` | Coder ↔ reviewer loop for high-blast-radius changes | 2-3x normal edit |

Daily floor: hooks + `/check` on every PR. Per-PR gate: `/audit-security` for security-sensitive changes. Hard cases: `/safe-edit` for money path / migrations / auth changes.

## What lives here vs. what doesn't

- **In `.claude/`**: agent and command definitions, hooks, and project-scoped `settings.json`. Useful to every contributor and every Claude session against this repo.
- **Not in `.claude/`**: `settings.local.json` (per-user), runtime locks (`*.lock`), or anything user-specific. The repo `.gitignore` keeps those out.

## Adding a new rule to the hook

Each rule in `hooks/security-patterns.sh` is a block with this shape:

```bash
# ----- RULE: <stable-name> -------------------------------------------
# Why: <one paragraph — what bug class does this prevent? Cite a real
# incident if there's one in the repo's history.>
while IFS= read -r m; do
  ln="${m%%:*}"
  register "<rule-name>" "$ln" \
    "<short why for the in-line report>" \
    "<the safer alternative; usually 'import X from app.utils.Y'>"
done < <(hits '<grep -E pattern>')
```

Rules MUST:
- Have a stable name that's used in `# noqa: <name>` bypasses.
- Match a pattern that's both detectable and meaningful — loose enough to catch future bugs of the same class, tight enough that the false-positive rate is < 10%.
- Suggest a concrete fix — never just "this is bad."
- Be scoped to the file types where the pattern is meaningful (Python-only rules go inside the Python `if`).

## Adapting these as the project grows

The agents cite concrete file paths. As the codebase shifts, edit the agents to:

- Cite the actual file paths for the auth middleware, tenant-scoping helper, migration directory, test directory.
- Replace generic invariants with the concrete library / type / column type the project chose.
- Add project-specific invariants as new ADRs get written (the project's ADRs live under `docs/` once they exist).
- Append new "Known bug shapes" entries to `repo-security-auditor.md` whenever a real regression gets fixed — that section is the institutional memory.

The pattern is "keep the framework, swap in the specifics." Don't rewrite from scratch each time the stack shifts.
