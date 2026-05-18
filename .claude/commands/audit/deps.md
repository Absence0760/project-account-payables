---
description: Cross-workspace dependency audit (pnpm + Dependabot config + GitHub Actions pinning)
---

Sweep dependencies across every workspace for known CVEs and version drift; verify Dependabot covers everything and CI workflow pins aren't a supply-chain risk.

## What this is

The repo has four workspaces with their own `package.json`:

- `frontend/` — SvelteKit 5, Vite, vitest
- `backend/` — Hono, `<cms-client>`, esbuild, vitest
- `studio/` — <CMS> Studio v5, React 19
- `infra/` — no Node deps (Terraform); skip

Plus:

- **Root `package.json`** — workspace orchestration + pnpm overrides (currently `js-yaml@<3.14.2` and `cookie@<0.7.0`)
- **GitHub Actions** — `.github/workflows/*.yml` — action SHA pinning vs `@v6` floating tags
- **Dependabot config** — `.github/dependabot.yml` — must cover every workspace + GitHub Actions

There's already a scheduled `audit.yml` workflow that runs `pnpm audit` weekly and files an issue on findings. This command does the equivalent sweep on demand plus the supply-chain checks the scheduled job doesn't cover (Dependabot config drift, workflow pinning).

## What to check

1. **`pnpm audit` per workspace.** Run from the repo root:
   ```
   pnpm -r --filter @my-project/frontend audit --audit-level=moderate
   pnpm -r --filter @my-project/backend  audit --audit-level=moderate
   pnpm -r --filter @my-project/studio   audit --audit-level=moderate
   ```
   Collect moderate+ findings. For each: package, version, CVE, fix version, manifest path. The canonical resolution shape in this repo is the cookie override added in commit `79befae` — a transitive that upstream hasn't fixed gets pinned via the root `package.json`'s `pnpm.overrides` block.

2. **Open audit issue.**
   ```
   gh issue list --label dependency-audit --state open
   ```
   If one exists, surface its title — that's the scheduled `audit.yml`'s most recent flagged set. Confirm whether the findings match what `pnpm audit` returns today.

3. **Dependabot coverage.** Read `.github/dependabot.yml`. The expected shape is:
   - `package-ecosystem: "npm"` × 3 — one entry per workspace at `/frontend`, `/backend`, `/studio`.
   - `package-ecosystem: "github-actions"` × 1 — `directory: "/"` (Dependabot scans `.github/workflows/` from this root).
   - Schedule weekly; grouped where it reduces PR churn (svelte-ecosystem, <cms>, types, <backend-framework>).
   - **No npm entry at `/`** — the root `package.json` only holds workspace orchestration + pnpm overrides; nothing for Dependabot to bump.
   - Flag any missing workspace entry, any non-weekly schedule, or any ungrouped flood of related packages.

4. **Lockfile-sync workflow exists.** Dependabot edits `<workspace>/package.json` but never touches the root `pnpm-lock.yaml`, which breaks `ci.yml`'s `pnpm install --frozen-lockfile`. The compensating workflow is `.github/workflows/dependabot-lockfile.yml` — it regenerates the lockfile on every Dependabot PR and commits the result back so CI retriggers and the PR can go green without manual intervention. Verify:
   - The workflow file exists.
   - It uses `DEPENDABOT_LOCKFILE_PAT` (a fine-grained PAT with `Contents: Write`), not `GITHUB_TOKEN` — GitHub blocks the latter from retriggering `pull_request` events.
   - The PAT is scoped to this repo and has an expiry. If it's stale or revoked, dep PRs pile up unmerged.

5. **GitHub Actions pinning.** Grep `.github/workflows/` for `uses: <action>@<ref>`.
   - Floating refs (`@main`, `@v6`) are supply-chain risks for actions that can be force-pushed by the publisher.
   - SHA pins (`@<sha>`) are the safer default for workflows that touch `${{ secrets.* }}` or deploy.
   - Flag floating refs on `deploy-frontend.yml`, `deploy-backend.yml`, `deploy-studio.yml`, and `claude.yml` (which has access to project tokens). `ci.yml`, `codeql.yml`, `audit.yml` are lower-stakes but worth surfacing too.

6. **Override hygiene.** Read the root `package.json` `pnpm.overrides` block. For each override:
   - Confirm it's still needed — has upstream shipped a fix that lets us drop the override? Pull the latest version of the package from npm and check.
   - Confirm the override range is tight (e.g. `^0.7.0` not `>=0.7.0` — see the cookie override discussion).
   - Confirm there's a comment or commit message explaining *why* (the original CVE).

7. **Node engines.** Root `package.json` declares `"engines": { "node": ">=22" }`. Confirm:
   - CI workflows use `node-version: 22` (not `18`, not `20`, not unspecified).
   - Lambda runtime in `infra/lambda.tf` matches (`nodejs22.x` or newer; `nodejs20.x` deprecates 2026-Q2).

8. **Local toolchain drift.** Optional but worth flagging if obvious: check the user's `~/.bashrc.d/` setup gives a recent enough pnpm + node. The `update-all` function in `~/.bashrc.d/32-functions-update.sh` covers system tools; if local node/pnpm versions are wildly out of sync with what CI uses, flag it.

## Report

- **Critical** — known-exploited CVE in a runtime path (the production Lambda or the deployed frontend bundle), not just a dev-only transitive.
- **High** — a CVE with a fix available; a deploy workflow using a floating action ref; Dependabot missing an entire workspace.
- **Medium** — version drift with no CVE but the upgrade is overdue; loose override range; Node engines mismatch between repo and Lambda runtime.
- **Low** — undocumented override, floating ref in a non-deploy workflow, dependabot grouping that could be tightened.

For each finding: package + version + advisory link + the file to change + the upgrade command (or the override expression).

## Useful starting points

- `package.json` (root) — workspace orchestration + overrides
- `frontend/package.json`, `backend/package.json`, `studio/package.json`
- `.github/workflows/*.yml`
- `.github/dependabot.yml`
- `infra/lambda.tf` — runtime declaration
- The latest `audit.yml` workflow run for the current findings set

## Delegate to

Use a `general-purpose` agent — the work is mostly running each tool in turn and reading the output. Pass this file as the prompt body.

Read-only audit. Recommend upgrades; don't apply them without instruction (a major bump is its own conversation).
