---
description: Find server-only secrets that may have leaked into a client bundle, git history, GitHub Actions logs, or a public asset
---

Audit for secrets / env vars / API keys that should be server-only but are reachable from a client bundle, a public asset, or git history.

## Goal

This repo uses SOPS-encrypted `*.sops` files for secrets at rest. The trust boundary is clear: anything in `frontend/.env` is `PUBLIC_*` and ships to the client; everything else stays server-only. Find any key that's on the wrong side, and confirm the SOPS pattern is actually being honoured (no committed plaintext, no leaked history).

## What to check

1. **SOPS files are actually encrypted.**
   - `backend/.env.sops`, `infra/terraform.tfvars.sops`: open and confirm each file matches the unstructured-JSON SOPS shape:
     - A single top-level `"data": "ENC[AES256_GCM,data:...,iv:...,tag:...,type:str]"` blob holding the encrypted body.
     - A `"sops"` metadata object with `kms` recipient ARNs, `mac` (`ENC[...]`), `unencrypted_suffix`, `version`.
   - The whole-file blob shape is normal for SOPS-encrypted `.env` / `.tfvars` (unstructured input) — distinct from YAML SOPS where each value is individually `ENC[...]`. Either is fine; reject any file that's plaintext at the top level.
   - A SOPS file that's been edited without `sops <file>` (e.g. via `vim` on the encrypted blob) loses encryption integrity — the `mac` won't validate. Flag if you can confirm a recent direct edit.

2. **Plaintext SOPS siblings absent from git.**
   - `backend/.env`, `infra/terraform.tfvars`: confirmed gitignored.
   - `git log --all --full-history -- backend/.env infra/terraform.tfvars` should return zero commits ever. If it returns any, the secret is permanently exposed and every value in it needs rotation — flag as Critical.

3. **`.env` files at workspace roots.**
   - `frontend/.env`, `studio/.env`: gitignored. `frontend/.env.example` and `studio/.env.example` are the committed templates (PUBLIC_* / SANITY_STUDIO_* only; no real secrets).
   - Run `git log --all --full-history -- frontend/.env studio/.env` to confirm neither has ever been committed.

4. **Client-bundle leakage (frontend).**
   - SvelteKit env vars are split: `$env/static/public` is inlined into the client bundle, `$env/static/private` is server-only. Per `frontend/CLAUDE.md`, the frontend stays static — there's no `$env/dynamic/private` anywhere; if it appears, that's a Critical because it implies an SSR adapter was added.
   - Grep `frontend/src/` for `$env/static/private`, `$env/dynamic`. Every hit is a finding.
   - Grep `frontend/src/` for raw `process.env` references. SvelteKit's static build doesn't expose `process.env` to the client — any reference is either dead code or a bug.

5. **Server-only env touched from a non-server frontend path.**
   - The frontend has no server-only paths today (static adapter). Any reference to `$env/static/private` or to a non-`PUBLIC_*` env var from `frontend/src/` is a finding.

6. **Backend env hygiene.**
   - `backend/src/` references `process.env.X` directly (per `backend/CLAUDE.md`, no `dotenv` imports reachable from `lambda.ts`).
   - Grep `backend/src/lambda.ts` and everything it transitively imports for `import 'dotenv'` or `dotenv/config`. Any hit is a finding — dotenv must only live in `server.ts`.
   - `backend/.env.example` lists the env-var names. Compare against the SOPS-encrypted `backend/.env.sops` (run `sops -d backend/.env.sops` if you have `kms:Decrypt` and report by name, never by value) — any key in `.env.example` missing from the encrypted file is a Medium; any extra key in the encrypted file is a Low.

7. **GitHub Actions workflow secrets.**
   - `.github/workflows/*.yml`: every `env:` block should reference `${{ secrets.X }}` or `${{ vars.X }}`, never a literal value.
   - `actions-runner` / build steps should not `echo $SECRET_X` or `set -x` with secret values in scope.
   - Workflows that deploy (`deploy-frontend.yml`, `deploy-backend.yml`, `deploy-studio.yml`) must use OIDC (`aws-actions/configure-aws-credentials@v4+` with `role-to-assume`), never `aws-access-key-id` / `aws-secret-access-key`. Long-lived AWS keys in any workflow is a Critical.

8. **Public asset leak.**
   - Search `frontend/static/` for any file containing key shapes — `sk_`, `rs_` (<email-service> keys), `key-`, `Bearer `, hex strings ≥ 32 chars. Pasted-into-asset-config leaks have happened.

9. **Git history pickaxe.**
   - `git log --all -S '<EMAIL_SERVICE>_API_KEY' -S '<CMS>_API_TOKEN' -S '<PAYMENT>_PASSPHRASE' -S 'AWS_ACCESS_KEY' -S 'sk-' --source --pretty=fuller`
   - The `-S` "pickaxe" finds commits that added or removed the literal string. A single touch on a real secret means that value is permanently exposed and needs rotation regardless of subsequent removal — flag as Critical with the recommendation "rotate the underlying credential, the value can be recovered from git history."

10. **`.gitignore` covers the right paths.**
    - Confirm `.gitignore` ignores: `backend/.env`, `frontend/.env`, `studio/.env`, `infra/terraform.tfvars`, `infra/*.tfstate*`, `.envrc`. Any missing → Medium.

## Report

- **Critical** — a real secret in git history, an SSR adapter that exposes server-only env to the client, an AWS access key in a workflow, an unencrypted `*.sops` file committed.
- **High** — server-only env referenced from a non-server frontend path, dotenv reachable from Lambda bundle, workflow logs an env var, OIDC role's `:sub` condition missing or wildcarded.
- **Medium** — env var in `.env.example` missing from the encrypted file, key in the encrypted file with no documented purpose, `.gitignore` missing a path.
- **Low** — undocumented env intent, missing example entry, overscoped key (e.g. a write-scope <CMS> token used only for reads).

For each: the literal env-var name and the file:line, what should change. **Never paste a found key value into the report — identify by name + location only.**

## Useful starting points

- `backend/.env.example`, `infra/terraform.tfvars.example` — declared env shapes
- `backend/CLAUDE.md § Three entry points` — dotenv-isolation rationale
- `frontend/CLAUDE.md § Hard rules` — static-only invariant
- `.github/workflows/deploy-*.yml` — OIDC pattern
- `.sops.yaml` — KMS recipient configuration
- `docs/deployment.md § Secrets management` — the full workflow
- `docs/security.md § Risk 8` (secrets management) — the risk register entry

## Delegate to

Use the `repo-security-auditor` agent: `"Audit for server-only secrets that may have leaked into a client bundle, public asset, GitHub Actions log, or git history."`

Read-only. Recommendations only — never paste a found key into the report. Identify by name + location.
