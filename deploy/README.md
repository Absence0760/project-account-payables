# deploy/ — minimal single-VM production stack

Operational files for the ~$20/month deployment described in
[`docs/minimal-deployment.md`](../docs/minimal-deployment.md) (read that
first — it holds the architecture, cost model, and the how-to-add-it-later
paths for everything this footprint leaves out).

The whole flow is four commands on a fresh VM:

```
./bootstrap-vm.sh                     # once: docker, compose, sops, swap, cron, IMDS fix
# copy the sops env in as deploy/.env.sops, log out/in (docker group), then:
./deploy.sh                           # every deploy: build, migrate, roll, verify
./add-tenant.sh acme --name "Acme" --admin-email admin@acme.com
```

| File | Purpose |
|---|---|
| `bootstrap-vm.sh` | One-time, idempotent VM setup (Amazon Linux 2023): docker + compose plugin + sops + cronie (AL2023 ships no cron daemon) + AWS CLI, 2 GB swap, nightly backup cron, IMDSv2 hop-limit fix. Other distros get the manual list. |
| `compose.prod.yml` | Postgres (pgvector) + Redis (AOF) + API + Caddy. No DB host ports; S3 is real AWS. API healthcheck lets deploys verify themselves. Container logs capped (json-file, 10 MB × 5 per service) so they can't fill the 30 GB disk. `FEOH_DATABASE_URL`/`FEOH_REDIS_URL` are override seams for RDS/ElastiCache later. |
| `Caddyfile` | TLS + static SPA + `api.feohledger.com` reverse proxy. Domains via env. |
| `tenants.caddy.example` | Template for the per-VM tenant host list (`tenants.caddy`, gitignored). `add-tenant.sh` maintains it — manual edits rarely needed. |
| `deploy.sh` | Preflight → pull → decrypt secrets → dockerized frontend build (no Node/pnpm on the VM) → backend build → migrate (control plane + all tenants) **before** rolling → `up -d --wait` → Caddy reload. Flags: `--no-pull`, `--backend-only`, `--frontend-only`. |
| `add-tenant.sh` | Tenant DB + org + admin user (same `provision_tenant` path as signup) + Caddy host block + reload, in one shot. Generates a temp password (first-login change forced) unless `--admin-password` given. |
| `backup.sh` | Nightly pg dumps (globals + control plane + every `feoh_*` DB) streamed to S3. Cron installed by bootstrap. |
| `env.example` | Contract for the sops-encrypted env — `deploy.sh` validates the required keys against it. |

## Before the VM (once per project)

- AWS account + the `infra/` Terraform module applied (S3 buckets incl. the
  lifecycle-expired backups bucket — its `backups_bucket` output feeds
  `BACKUP_S3_BUCKET` — and the KMS key).
- EC2 `t4g.small` (Amazon Linux 2023 arm64 recommended), 30 GB gp3, ports
  80/443 open. Instance profile: `kms:Decrypt` on the sops key; S3 read/write
  (`s3:GetObject/PutObject/AbortMultipartUpload/ListBucket`) on the
  invoice-files, audit-logs, and backup buckets; `ses:SendEmail` if using
  SES; ideally `ec2:ModifyInstanceMetadataOptions` so bootstrap can fix the
  IMDS hop limit itself.
- DNS: three records → this VM: `app.feohledger.com`, `api.feohledger.com`, and a
  **wildcard** `*.app.feohledger.com` (the wildcard makes tenant onboarding
  DNS-free; it needs no wildcard certificate — Caddy issues per-host certs).
- Secrets: author a real-valued copy of `env.example`, encrypt with sops into
  the **private** `infra-secrets` repo (per-project subdir + KMS key — see
  `~/github/project-mgmt/docs/secrets-management.md`), copy the encrypted
  file onto the VM as `deploy/.env.sops`. Never commit either file here —
  this repo is public.

## Deploys

`./deploy.sh` — it preflights its own prerequisites and required env keys,
runs migrations before the new API serves traffic, and fails loudly (via the
compose healthcheck) if the API doesn't come up. If the build or migration
step fails, the previously-running containers keep serving.

## Tenants

`./add-tenant.sh <slug> --name "Company" --admin-email admin@company.com` —
provisions everything and prints the login URL + temp password. Don't run
`scripts/seed.py` (demo data) in prod.

## Backups

Installed by bootstrap as `/etc/cron.d/feoh-backup` (03:17 UTC nightly,
logging to `/var/log/feoh-backup.log`). Restore a single DB (test this once
before calling backups done):
`aws s3 cp s3://<bucket>/pg/<date>/<db>.dump - | docker compose -f compose.prod.yml exec -T postgres pg_restore -U postgres --create -d postgres`
