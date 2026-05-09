# bin/

Operator scripts. Most wrap an AWS / sops / terraform sequence so the deploy and rotation flows fit on one line and are idempotent.

All scripts source `bin/lib/common.sh` for the color helpers + `need_cmd` / `need_aws_auth` checks.

## First deploy (or rebuild)

| When | Run |
|---|---|
| Before any `terraform apply` | `bin/aws-preflight.sh` |
| AWS session expired | `bin/aws-login.sh` |
| After `terraform apply` on `envs/<env>` to wire sops to the new KMS keys | `bin/sops-init.sh <env>` |
| To put a real secret into the env | `echo -n "$VALUE" \| bin/secret-set.sh <env> <KEY>` then `cd infra/envs/<env> && terraform apply` |

## Rare events

| When | Run |
|---|---|
| KMS key was destroyed + recreated, encrypted file is stuck on the old key | `bin/key-rotate.sh <env>` |

## Conventions

- **Read-only by default.** The scripts that mutate state (`secret-set`, `key-rotate`) prompt or take input via stdin so secrets never appear on the command line.
- **Idempotent.** Re-running on an already-completed step prints "skipping" / "already done" and exits 0.
- **Profile selection.** Scripts honour `$AWS_PROFILE` (set it once, e.g. `export AWS_PROFILE=account-payables`). `bin/aws-login.sh` defaults to a profile named `account-payables` if `$AWS_PROFILE` is unset.
- **Region.** Pinned to `us-east-1` everywhere.

## What's not here yet

The reference projects we copied this pattern from also include scripts for:
- `deploy-preview.sh` — applies bootstrap → github-oidc → envs/preview in order
- `preview-status.sh` — health check after a deploy
- `lambda-logs.sh` — tails Lambda logs
- `disaster-recovery.sh` — full stack rebuild
- `cancel-stale-runs.sh` — kills GitHub Actions ghost runs
- `onboard-operator.sh` — adds a second human to a KMS key policy

Those depend on the runtime stack (Lambda / ECS / etc.), which isn't wired up here yet. Add them after the runtime module lands in `infra/envs/<env>/main.tf`.
