# deploy/ — minimal single-VM production stack

Operational files for the ~$20/month deployment described in
[`docs/minimal-deployment.md`](../docs/minimal-deployment.md) (read that
first — it holds the architecture, cost model, and upgrade triggers).

| File | Purpose |
|---|---|
| `compose.prod.yml` | Postgres (pgvector) + Redis + API + Caddy. No DB host ports; S3 is real AWS. |
| `Caddyfile` | TLS + static SPA + `api.<domain>` reverse proxy. Domains via env. |
| `tenants.caddy.example` | Template for the per-VM tenant host list (`tenants.caddy`, gitignored). |
| `deploy.sh` | Pull → decrypt secrets → build frontend + backend → migrate (all tenant DBs) → roll containers. |
| `backup.sh` | Nightly pg dumps (control plane + every `ap_*` DB) streamed to S3. |
| `env.example` | Contract for the sops-encrypted env. |

## VM prerequisites (once)

- EC2 `t4g.small` (or similar), 30 GB gp3, ports 80/443 open; 2 GB swap.
- Installed: docker + compose plugin, git, Node 20 + pnpm (Corepack), `sops`,
  AWS CLI v2.
- Instance profile: `kms:Decrypt` on the project sops key; S3 read/write on
  the invoice-files, audit-logs, and backup buckets; `ses:SendEmail` if using
  SES.
- **IMDSv2 hop limit**: containers cannot reach instance-profile credentials
  through Docker's NAT with the default hop limit of 1. Fix once:
  `aws ec2 modify-instance-metadata-options --instance-id <id> --http-tokens required --http-put-response-hop-limit 2`
- Clone this repo (e.g. `~/project-account-payables`).
- DNS `A` records → this VM: `app.<domain>`, `api.<domain>`, and one per
  tenant subdomain.

## Secrets

Author a real-valued copy of `env.example`, encrypt it with sops into the
**private** `infra-secrets` repo (per-project subdir + KMS key — see
`~/github/project-mgmt/docs/secrets-management.md`), then copy the encrypted
file onto the VM as `deploy/.env.sops`. `deploy.sh` decrypts it to
`deploy/.env` on every run; both are gitignored. Never commit either here —
this repo is public.

## First boot

1. `./deploy.sh` — builds everything, runs migrations, starts the stack.
2. Create the first tenant (no demo seed in prod — don't run `seed.py`):
   `docker compose -f compose.prod.yml exec api python scripts/create_tenant.py --name "Acme" --slug acme --admin-email admin@acme.com --admin-password <temp>`
3. Add the tenant's DNS record, append its block to `tenants.caddy` (see the
   example file), then:
   `docker compose -f compose.prod.yml exec caddy caddy reload --config /etc/caddy/Caddyfile`

## Deploys

`./deploy.sh` (flags: `--no-pull`, `--backend-only`, `--frontend-only`).
Migrations always run before the new API serves traffic and fan out to every
tenant DB.

## Backups

Cron it nightly (this is the DR story — do not skip):

```
17 3 * * * /home/ec2-user/project-account-payables/deploy/backup.sh >> /var/log/ap-backup.log 2>&1
```

Restore a single DB (test this once before calling backups done):
`aws s3 cp s3://<bucket>/pg/<date>/<db>.dump - | docker compose -f compose.prod.yml exec -T postgres pg_restore -U postgres --create -d postgres`
