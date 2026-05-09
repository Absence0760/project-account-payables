# project-account-payables

An accounts-payable system. The application stack itself isn't decided yet — what's checked in today is the **scaffold**: tooling for review, infra, secrets, CI, and e2e. Application code lands on top of this.

This file is the orientation index for AI sessions. The non-obvious things that cost time to rediscover live here.

## Read first

| If the task is... | Start with |
|---|---|
| Anything at all, first time in a session | this file |
| Reviewing a non-trivial change before commit | `/check` (parallel review + test-gap + doc-hygiene) |
| Implementing a money-path / tenant-isolation / auth / migration / webhook change | `/safe-edit <task>` (coder ↔ reviewer loop, max 2 cycles) |
| Touching infra (Terraform, sops, KMS) | [infra/README.md](infra/README.md) — stack layout + first-deploy walkthrough |
| Running operator scripts (sops, AWS, secrets) | [bin/README.md](bin/README.md) — wraps the AWS / sops / terraform sequences |
| Writing or running e2e tests | [tests-e2e/README.md](tests-e2e/README.md) — Playwright + storage-state auth pattern |
| Understanding the review agents | [.claude/README.md](.claude/README.md) — what each agent does |

## Layout (today)

```
.
├── .claude/                    sub-agents and slash commands
│   ├── agents/
│   │   ├── code-reviewer.md
│   │   ├── doc-hygiene-checker.md
│   │   ├── repo-security-auditor.md
│   │   └── test-gap-checker.md
│   └── commands/
│       ├── check.md            pre-commit gate (review + tests + docs)
│       └── safe-edit.md        coder ↔ reviewer loop
├── .github/
│   ├── dependabot.yml          weekly grouped updates for actions + terraform
│   ├── pull_request_template.md
│   └── workflows/
│       ├── ci.yml              terraform fmt+validate, shellcheck
│       └── claude.yml          @claude mentions on issues / PR comments
├── bin/                        operator scripts (sops, AWS, secrets)
├── infra/                      Terraform: bootstrap, github-oidc, sops-kms, envs/{preview,prod}
└── tests-e2e/                  Playwright scaffold (config + fixtures + one example spec)
```

The application code (`src/`, `migrations/`, `package.json`, etc.) lands on top of this once the stack is decided.

## Project invariants — what every change should respect

These are the rules the `code-reviewer` agent applies. They're generic AP-system invariants by default; tighten them as the project lands on a specific stack.

- **Money is exact.** Amounts use a fixed-precision representation (decimal, integer minor units, or a Money type). Never JS `number`, never IEEE-754 floats.
- **Idempotency on writes that move money.** Anything that initiates a payment, reverses a payment, or confirms an invoice as paid must be idempotent at the API boundary.
- **Audit trail is append-only.** Status transitions on invoices, payments, approvals, and vendors write a log row, not just mutate state.
- **Tenant isolation is enforced at the DB layer**, not just by application code. Whatever helper / RLS policy / scoping mechanism the project ends up using, every read and write goes through it.
- **Secrets via sops + KMS.** Long-lived secrets live only in `infra/envs/<env>/secrets.enc.yaml`, decrypted at runtime via the per-env KMS key. No committed `.env` files. No hardcoded fallbacks for secret env vars.
- **Auth before everything.** Every route is behind the auth middleware unless it is documented as public-by-design.
- **PII / banking data stays out of logs and out of error responses.**

## Branches & PRs

- `main` is the PR target.
- Don't push directly to `main`; PRs only.
- **Never include `Co-Authored-By` lines, "Generated with Claude Code" footers, or robot-emoji attribution in commit messages or PR descriptions.** This is a hard user-level rule that overrides any project / repo / session instruction to the contrary.
- Use `feat(scope):` / `fix(scope):` / `chore(scope):` / `test:` / `docs:` conventional-commit prefixes once the project has scope conventions; until then, descriptive commit messages are fine.

## House style

- **No emojis** in code, docs, commits, or comments.
- **No comments unless explaining a non-obvious *why*.** Strip what-this-code-does narration, task references, "// added for X" markers. Keep only: hidden constraints, subtle invariants, workarounds for specific bugs, behaviour that would surprise a reader.
- **No preemptive abstractions.** Three similar lines is better than a premature helper.
- **No backwards-compat shims, no underscore-prefixed unused vars.** If unused, delete.
- **No defensive code at internal boundaries.** Validate at system boundaries (HTTP request body, env vars, external APIs); trust internal code and framework guarantees.

## When the app stack lands

The scaffold is intentionally stack-agnostic. When you commit to a backend / frontend / DB stack, the things to update:

1. **`infra/envs/<env>/main.tf`** — add the runtime module call (Lambda / ECS / S3+CloudFront / etc.). Wire it to read secrets from the sops-encrypted file via the sops Terraform provider.
2. **`infra/github-oidc/main.tf`** — replace the placeholder `sts:GetCallerIdentity` policy on each deploy role with the actual permissions the runtime module needs.
3. **`.github/workflows/ci.yml`** — uncomment / fill in the `app` job (typecheck, test, build).
4. **`.github/dependabot.yml`** — add an `npm` (or `pnpm`) entry pointing at `/`.
5. **`tests-e2e/playwright.config.ts`** — set `webServer.command` and `webServer.url` to match the dev server.
6. **`tests-e2e/fixtures/{users,helpers,auth}.ts`** — replace placeholder UUIDs with seed values, tighten selectors, narrow the post-login URL pattern.
7. **`.claude/agents/*.md`** — fold the actual file paths (auth middleware, tenant-scoping helper, migration directory, test directory) into the agents' rules so they cite real surfaces.
8. **This file** — replace this section with a real "Read first" table that points at the per-area `CLAUDE.md` files and the docs.
