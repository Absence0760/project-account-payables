#!/usr/bin/env bash
#
# key-rotate.sh — re-encrypt an env's secrets.enc.yaml under the
# current KMS key.
#
# Use case: you've changed the KMS key (via terraform — destroyed +
# recreated, or moved to a different key in `.sops.yaml`), and the
# encrypted file still has the OLD key in its sops metadata. sops
# auto-decrypts against whatever key it was encrypted with, so the
# file keeps WORKING — but the next operator with access only to the
# new key can't decrypt it. This script reads the file with the old
# key, re-encrypts under the new key recorded in `.sops.yaml`.
#
# Note on AWS-native KMS rotation: enabling automatic key rotation
# (`aws kms enable-key-rotation`, which the sops-kms module sets to
# true by default) does NOT require this script — rotation under the
# same key alias is transparent to sops. Use this script only when
# the *key itself* (not just the key material) changes.
#
# Usage:
#   bin/key-rotate.sh <env>
#   bin/key-rotate.sh preview
#   bin/key-rotate.sh prod

set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

if [[ $# -ne 1 ]]; then
	cat >&2 <<EOF
Usage: bin/key-rotate.sh <env>
  env  preview | prod
EOF
	exit 2
fi

ENV_NAME="$1"
case "$ENV_NAME" in
	preview|prod) ;;
	*) fatal "Unknown env: $ENV_NAME" ;;
esac

need_cmd sops
need_cmd grep
need_aws_auth

SECRETS_FILE="$REPO_ROOT/infra/envs/$ENV_NAME/secrets.enc.yaml"
SOPS_CONFIG="$REPO_ROOT/infra/.sops.yaml"

if [[ ! -f "$SECRETS_FILE" ]]; then
	fatal "$SECRETS_FILE missing — nothing to rotate"
fi

step "Current sops metadata for $ENV_NAME"
# sops writes the encryption metadata (KMS ARN, lastmodified, mac) as
# *plaintext* alongside the encrypted payload. Read it directly with
# grep — `sops --decrypt --extract` doesn't apply to metadata.
current_arn="$(grep -oE 'arn:aws:kms:[a-z0-9-]+:[0-9]+:key/[a-f0-9-]+' "$SECRETS_FILE" | head -1)"
if [[ -n "$current_arn" ]]; then
	ok "Encrypted under: $current_arn"
else
	warn "Couldn't read current KMS ARN from $SECRETS_FILE — proceeding anyway"
fi

# Read the target ARN from .sops.yaml — that's the source of truth
# for "the key we want this file under".
case "$ENV_NAME" in
	preview) target_arn_line="$(grep -A1 'preview/secrets' "$SOPS_CONFIG" | grep 'kms:' || true)" ;;
	prod)    target_arn_line="$(grep -A1 'prod/secrets'    "$SOPS_CONFIG" | grep 'kms:' || true)" ;;
esac
target_arn="$(echo "$target_arn_line" | grep -oE 'arn:aws:kms:[a-z0-9-]+:[0-9]+:key/[a-f0-9-]+' | head -1 || true)"
# Fall back to the placeholder text when no ARN matches, so the next
# guard can fail with a useful "still has placeholder" message.
if [[ -z "$target_arn" ]] && echo "$target_arn_line" | grep -qE 'REPLACE_(PROD|PREVIEW)_KMS_ARN'; then
	target_arn="REPLACE_$(echo "$ENV_NAME" | tr '[:lower:]' '[:upper:]')_KMS_ARN"
fi
if [[ "$target_arn" == REPLACE_* ]] || [[ -z "$target_arn" ]]; then
	fatal "$SOPS_CONFIG has unresolved KMS placeholder for $ENV_NAME — run bin/sops-init.sh first"
fi
ok "Target key in .sops.yaml: $target_arn"

if [[ "$current_arn" == "$target_arn" ]]; then
	ok "Already encrypted under the target key — nothing to rotate"
	exit 0
fi

step "Re-encrypting"
log "Running 'sops updatekeys' against $SECRETS_FILE"
log "(reads with old key, re-writes under the new key from .sops.yaml)"
sops updatekeys --config "$SOPS_CONFIG" "$SECRETS_FILE"
ok "Rotation complete"

step "Verify"
if sops -d "$SECRETS_FILE" >/dev/null 2>&1; then
	ok "decrypts cleanly under the new key"
else
	fatal "Re-encrypted file does NOT decrypt — rotation may have left it inconsistent. Investigate before re-applying terraform."
fi

step "Push to runtime stack"
log "Re-apply the env so the new value lands in the running service:"
dim "  cd infra/envs/$ENV_NAME && terraform apply"
