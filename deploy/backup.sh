#!/usr/bin/env bash
# Nightly Postgres backup to S3 (docs/minimal-deployment.md § Backups).
# Dumps role definitions plus the control plane and EVERY feoh_* tenant DB
# (pg_dump custom format, already compressed), streaming straight to S3 —
# nothing persists on local disk. Credentials come from the EC2 instance
# profile; the target bucket from BACKUP_S3_BUCKET (env, or deploy/.env).
#
# Cron (see deploy/README.md):
#   17 3 * * * /path/to/repo/deploy/backup.sh >> /var/log/feoh-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f compose.prod.yml)

BUCKET="${BACKUP_S3_BUCKET:-}"
if [ -z "$BUCKET" ] && [ -f .env ]; then
	BUCKET=$(grep -E '^BACKUP_S3_BUCKET=' .env | tail -1 | cut -d= -f2- || true)
fi
if [ -z "$BUCKET" ]; then
	echo "BACKUP_S3_BUCKET not set (env var or deploy/.env)." >&2
	exit 1
fi

# On EC2 the aws CLI infers the region from IMDS; off-EC2 (the Hetzner
# variant) there is no IMDS, so fall back to the AWS_REGION the sops env
# already carries.
if [ -z "${AWS_DEFAULT_REGION:-}" ] && [ -z "${AWS_REGION:-}" ] && [ -f .env ]; then
	REGION=$(grep -E '^AWS_REGION=' .env | tail -1 | cut -d= -f2- || true)
	if [ -n "$REGION" ]; then
		export AWS_DEFAULT_REGION="$REGION"
	fi
fi

STAMP=$(date -u +%F)
PREFIX="s3://${BUCKET}/pg/${STAMP}"

# Roles / globals — tiny, plain SQL.
"${COMPOSE[@]}" exec -T postgres pg_dumpall -U postgres --globals-only |
	gzip | aws s3 cp - "${PREFIX}/globals.sql.gz"

DBS=$("${COMPOSE[@]}" exec -T postgres psql -U postgres -Atc \
	"SELECT datname FROM pg_database WHERE datname = 'feohledger' OR datname LIKE 'feoh\\_%' ORDER BY datname")

for db in $DBS; do
	"${COMPOSE[@]}" exec -T postgres pg_dump -U postgres -Fc "$db" |
		aws s3 cp - "${PREFIX}/${db}.dump"
done

echo "backup complete: ${PREFIX} ($(echo "$DBS" | wc -w) databases + globals)"
