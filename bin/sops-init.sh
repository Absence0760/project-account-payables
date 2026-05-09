#!/usr/bin/env bash
#
# sops-init.sh — resolves the placeholder KMS ARNs in
# `infra/.sops.yaml` after `terraform apply` on the per-env stack
# creates the keys, and seeds an empty `secrets.enc.yaml` for any env
# that doesn't have one yet.
#
# Idempotent: re-running detects already-resolved placeholders and
# already-seeded files, prints a "nothing to do" status, and exits 0.
#
# Usage:
#   bin/sops-init.sh                   # all envs that have terraform state
#   bin/sops-init.sh preview           # just preview
#   bin/sops-init.sh prod              # just prod
#   bin/sops-init.sh preview prod      # both, explicit
#
# Prereqs:
#   - sops, aws, jq, terraform on PATH
#   - aws sts get-caller-identity succeeds (i.e. SSO login active)
#   - `terraform apply` already ran on each env you want to bootstrap
#     so `terraform output -raw kms_key_arn` returns a value
#
# Recovery: if you blow away an env and recreate it, the new KMS ARN
# replaces the old one in `.sops.yaml`. Existing encrypted files
# decrypt against whatever key they were encrypted with (the metadata
# is in the file), so they keep working until you `sops updatekeys`
# them under the new key — see bin/key-rotate.sh.

set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

SOPS_CONFIG="$REPO_ROOT/infra/.sops.yaml"

cd "$REPO_ROOT"

declare -a ENVS
if [[ $# -eq 0 ]]; then
	ENVS=(preview prod)
else
	ENVS=("$@")
fi

for e in "${ENVS[@]}"; do
	case "$e" in
		preview|prod) ;;
		*) fatal "Unknown env: $e (expected preview or prod)" ;;
	esac
done

step "Checking prereqs"
for cmd in sops aws jq terraform; do
	need_cmd "$cmd"
	ok "$cmd installed"
done

need_aws_auth
ok "AWS auth OK ($(aws sts get-caller-identity --query Arn --output text))"

if [[ ! -f "$SOPS_CONFIG" ]]; then
	fatal "$SOPS_CONFIG missing — restore from git"
fi

placeholder_for() {
	case "$1" in
		preview) echo "REPLACE_PREVIEW_KMS_ARN" ;;
		prod)    echo "REPLACE_PROD_KMS_ARN" ;;
	esac
}

env_dir_for() {
	echo "$REPO_ROOT/infra/envs/$1"
}

# Pick a placeholder secret to seed each env with. The first read of a
# fresh secrets file needs at least one key for sops to consider it
# valid yaml — DATABASE_URL is the canonical "this is a runtime secret"
# stub. Operators replace it with the real value via secret-set.sh.
SEED_KEY="DATABASE_URL"

for env in "${ENVS[@]}"; do
	step "Bootstrapping env: $env"
	env_dir="$(env_dir_for "$env")"
	placeholder="$(placeholder_for "$env")"
	secrets_file="$env_dir/secrets.enc.yaml"

	pushd "$env_dir" >/dev/null

	if ! terraform output -raw kms_key_arn >/dev/null 2>&1; then
		warn "terraform output is missing kms_key_arn for $env"
		warn "Run 'terraform init && terraform apply' in $env_dir first, then re-run this script."
		popd >/dev/null
		continue
	fi

	arn="$(terraform output -raw kms_key_arn)"
	popd >/dev/null

	if ! [[ "$arn" =~ ^arn:aws:kms:[a-z0-9-]+:[0-9]+:key/[a-f0-9-]+$ ]]; then
		fatal "$env: terraform returned an unexpected kms_key_arn: $arn"
	fi
	ok "$env KMS ARN: $arn"

	if grep -qF "$placeholder" "$SOPS_CONFIG"; then
		# Use a non-/ delimiter because the ARN contains slashes.
		sed -i "s|$placeholder|$arn|" "$SOPS_CONFIG"
		ok "Replaced $placeholder in infra/.sops.yaml"
	else
		ok "$placeholder already resolved in infra/.sops.yaml — skipping"
	fi

	if [[ -f "$secrets_file" ]]; then
		ok "$secrets_file already exists — leaving it alone"
	else
		log "Seeding $secrets_file (encrypted, with a placeholder key)"
		# Use `sops --output` instead of shell redirect: the redirect
		# truncates the target file BEFORE sops runs, so a sops failure
		# (KMS auth, network) leaves an empty file that breaks the
		# idempotence check on re-run.
		printf '%s: replace-me\n' "$SEED_KEY" \
			| sops --config "$SOPS_CONFIG" --input-type yaml --output-type yaml \
				--output "$secrets_file" --encrypt /dev/stdin
		if ! sops --decrypt "$secrets_file" >/dev/null 2>&1; then
			rm -f "$secrets_file"
			fatal "Seed of $secrets_file failed to decrypt round-trip; removed. Investigate KMS auth + .sops.yaml routing."
		fi
		ok "Seeded $secrets_file"
	fi
done

step "Verifying .sops.yaml is fully resolved"
if grep -qE 'REPLACE_(PROD|PREVIEW)_KMS_ARN' "$SOPS_CONFIG"; then
	warn ".sops.yaml still has placeholder ARNs — some envs aren't applied yet:"
	grep -nE 'REPLACE_(PROD|PREVIEW)_KMS_ARN' "$SOPS_CONFIG" >&2
	warn "Apply the missing env(s) and re-run this script."
	exit 0
fi
ok "All placeholders resolved"

step "Next steps"
log "Edit secrets:    sops infra/envs/<env>/secrets.enc.yaml"
log "Set one secret:  echo -n \"\$VALUE\" | bin/secret-set.sh <env> <KEY>"
log "Re-apply env:    cd infra/envs/<env> && terraform apply"
log "Verify decrypt:  sops --decrypt infra/envs/<env>/secrets.enc.yaml"
log ""
log "On every key rotation:"
log "  bin/key-rotate.sh <env>"
