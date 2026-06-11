#!/usr/bin/env bash
#
# sops-init.sh — one-time SOPS bootstrap for this repo, AWS KMS backend
#
# What this does:
#
#   1. Checks sops + aws + jq are installed
#   2. Verifies AWS CLI authentication (aws sts get-caller-identity)
#   3. Checks whether the project's KMS alias already exists; if not,
#      creates a new KMS key + alias in the configured region
#      IDEMPOTENT: a second run reuses the existing key, never creates a duplicate
#   4. Writes the alias ARN into .sops.yaml, replacing the placeholders
#   5. Seeds encrypted files from the examples if they don't already exist:
#        - infra/terraform.tfvars.sops
#        - backend/.env.sops
#   6. Tells you what to do next
#
# Cost: KMS keys are $1/month. One key per project means this repo will
# incur ~$1/month on your AWS bill. Delete the key via the AWS console (or
# with `aws kms schedule-key-deletion`) to stop the charge; note that KMS
# deletions have a 7–30 day pending window.
#
# Recovery: if you lose your laptop, your AWS login still grants access to
# the KMS key (via the account root user or any IAM identity with
# kms:Decrypt permission on it). Restore the repo on a new machine, run
# `aws configure`, and sops works again immediately. No key file backup
# required — AWS is the backup.
#
# Partial-failure recovery: if first-run fails BETWEEN `aws kms create-key`
# and `aws kms create-alias`, you'll have an orphan KMS key with no alias.
# The next run of this script won't find the alias (so describe-key fails),
# and will create a SECOND key — you don't want that. If you see this
# situation:
#
#   1. Find the orphan key:
#        aws kms list-keys --region <region> \
#          | jq '.Keys[].KeyId'
#      (The most recent one without an alias is probably the orphan; check
#       tags with `aws kms list-resource-tags --key-id <id>` to confirm.)
#   2. Either attach the expected alias to it:
#        aws kms create-alias \
#          --alias-name alias/account-payables-sops \
#          --target-key-id <orphan-key-id>
#      …then re-run this script (it'll find the alias and reuse the key).
#   3. Or schedule the orphan for deletion and re-run:
#        aws kms schedule-key-deletion \
#          --key-id <orphan-key-id> \
#          --pending-window-in-days 7
#
# The 7-day minimum pending-delete window means you will be billed for the
# orphan key for at least a week. The cost is trivial (~$0.23) but annoying.
#
# Usage:
#
#   ./bin/sops-init.sh
#
# Environment overrides:
#
#   KMS_REGION    — AWS region for the KMS key (default: us-east-1)
#   KMS_ALIAS     — Alias name without the "alias/" prefix
#                   (default: account-payables-sops)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOPS_CONFIG="$REPO_ROOT/.sops.yaml"
KMS_REGION="${KMS_REGION:-us-east-1}"
KMS_ALIAS_NAME="${KMS_ALIAS:-account-payables-sops}"
KMS_ALIAS_PATH="alias/$KMS_ALIAS_NAME"
REGION_PLACEHOLDER="KMS_REGION_PLACEHOLDER"
ACCOUNT_PLACEHOLDER="KMS_ACCOUNT_PLACEHOLDER"

# sops reads .sops.yaml starting from the current working directory. Force
# CWD to the repo root so creation_rules always resolve correctly regardless
# of where the operator invokes this script from.
cd "$REPO_ROOT"

# ----------------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------------

if [[ -t 1 ]]; then
	C_RESET=$'\033[0m'
	C_BOLD=$'\033[1m'
	C_GREEN=$'\033[32m'
	C_YELLOW=$'\033[33m'
	C_RED=$'\033[31m'
	C_BLUE=$'\033[34m'
else
	C_RESET=""; C_BOLD=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""
fi

step()  { printf "\n${C_BOLD}${C_BLUE}==> %s${C_RESET}\n" "$*"; }
log()   { printf "    %s\n" "$*"; }
ok()    { printf "    ${C_GREEN}v${C_RESET} %s\n" "$*"; }
warn()  { printf "    ${C_YELLOW}!${C_RESET} %s\n" "$*" >&2; }
err()   { printf "    ${C_RED}x${C_RESET} %s\n" "$*" >&2; }
fatal() { err "$*"; exit 1; }

# ----------------------------------------------------------------------------
# 1. Prerequisites
# ----------------------------------------------------------------------------

check_prereqs() {
	step "Checking prerequisites"
	local missing=0
	for tool in sops aws jq; do
		if command -v "$tool" >/dev/null 2>&1; then
			ok "$tool is installed"
		else
			err "$tool is not installed"
			missing=1
		fi
	done
	if (( missing )); then
		log "On macOS: brew install sops awscli jq"
		fatal "Install the missing tools and re-run."
	fi

	if aws sts get-caller-identity >/dev/null 2>&1; then
		AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
		AWS_CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text)"
		ok "AWS authenticated as $AWS_CALLER_ARN"
		log "Account: $AWS_ACCOUNT_ID"
		log "Region:  $KMS_REGION"
	else
		err "AWS CLI is not authenticated."
		log "Run: aws configure"
		log "Or set AWS_PROFILE to a profile that has kms:CreateKey permission."
		exit 1
	fi
}

# ----------------------------------------------------------------------------
# 2. KMS key — discover existing alias, or create a new key + alias
# ----------------------------------------------------------------------------

ensure_kms_key() {
	step "Ensuring KMS key exists ($KMS_ALIAS_PATH)"

	# Check whether the alias already resolves to a key. describe-key accepts
	# an alias name as the key ID, so this is a single-call existence check.
	local existing_key_id
	if existing_key_id=$(aws kms describe-key \
			--region "$KMS_REGION" \
			--key-id "$KMS_ALIAS_PATH" \
			--query 'KeyMetadata.KeyId' \
			--output text 2>/dev/null); then
		ok "Alias $KMS_ALIAS_PATH already resolves to key $existing_key_id"
		log "Reusing existing key — no new KMS resources created."
	else
		log "Creating a new KMS key for SOPS secrets..."
		local key_id
		key_id=$(aws kms create-key \
			--region "$KMS_REGION" \
			--description "SOPS secrets encryption for account-payables" \
			--key-usage ENCRYPT_DECRYPT \
			--tags TagKey=project,TagValue=account-payables \
			       TagKey=purpose,TagValue=sops-secrets \
			--query 'KeyMetadata.KeyId' \
			--output text)
		ok "Created KMS key $key_id"

		log "Creating alias $KMS_ALIAS_PATH..."
		aws kms create-alias \
			--region "$KMS_REGION" \
			--alias-name "$KMS_ALIAS_PATH" \
			--target-key-id "$key_id"
		ok "Alias created"

		# Turn on annual automatic key material rotation — a best-practice
		# that costs nothing and requires no action to maintain.
		aws kms enable-key-rotation \
			--region "$KMS_REGION" \
			--key-id "$key_id"
		ok "Annual key material rotation enabled"

		warn "You just created a new KMS key. Cost: ~\$1/month on your AWS bill."
		warn "See 'Tearing everything down' in infra/README.md to remove it."
	fi

	KMS_ARN="arn:aws:kms:${KMS_REGION}:${AWS_ACCOUNT_ID}:${KMS_ALIAS_PATH}"
	log "ARN for .sops.yaml: $KMS_ARN"
}

# ----------------------------------------------------------------------------
# 3. Wire the KMS ARN into .sops.yaml
# ----------------------------------------------------------------------------

update_sops_config() {
	step "Updating .sops.yaml with the KMS ARN"

	if [[ ! -f "$SOPS_CONFIG" ]]; then
		fatal ".sops.yaml is missing from the repo root — check out a clean copy"
	fi

	if grep -q "$REGION_PLACEHOLDER" "$SOPS_CONFIG" \
	   || grep -q "$ACCOUNT_PLACEHOLDER" "$SOPS_CONFIG"; then
		sed -i.bak \
			-e "s|$REGION_PLACEHOLDER|$KMS_REGION|g" \
			-e "s|$ACCOUNT_PLACEHOLDER|$AWS_ACCOUNT_ID|g" \
			"$SOPS_CONFIG"
		rm -f "$SOPS_CONFIG.bak"
		ok "Placeholders replaced — .sops.yaml now references $KMS_ARN"
	else
		if grep -q "kms:" "$SOPS_CONFIG"; then
			ok ".sops.yaml already has a populated KMS ARN — leaving untouched"
			log "Current kms entries:"
			grep "kms:" "$SOPS_CONFIG" | sed 's/^/      /'
		else
			warn ".sops.yaml has no placeholders AND no kms: entries — inspect manually"
		fi
	fi
}

# ----------------------------------------------------------------------------
# 4. Seed encrypted files from examples
# ----------------------------------------------------------------------------

seed_encrypted_file() {
	local plain_example="$1"
	local encrypted_out="$2"
	local label="$3"

	if [[ -f "$encrypted_out" ]]; then
		ok "$label already exists — leaving untouched"
		return
	fi

	if [[ ! -f "$plain_example" ]]; then
		warn "$plain_example not found — cannot seed $encrypted_out"
		return
	fi

	log "Seeding $label from $plain_example"
	# Place the file at the target path first so .sops.yaml creation_rules
	# match on the filename, then encrypt in place.
	cp "$plain_example" "$encrypted_out"
	sops --encrypt --in-place "$encrypted_out"
	ok "$label created and encrypted"
}

seed_encrypted_files() {
	step "Seeding encrypted files from examples"
	seed_encrypted_file \
		"$REPO_ROOT/infra/terraform.tfvars.example" \
		"$REPO_ROOT/infra/terraform.tfvars.sops" \
		"infra/terraform.tfvars.sops"
	seed_encrypted_file \
		"$REPO_ROOT/backend/.env.development" \
		"$REPO_ROOT/backend/.env.sops" \
		"backend/.env.sops"
}

# ----------------------------------------------------------------------------
# 5. Final instructions
# ----------------------------------------------------------------------------

print_next_steps() {
	step "Setup complete"
	cat <<EOF

${C_BOLD}Next steps:${C_RESET}

  1. Edit the encrypted files with sops:

       sops infra/terraform.tfvars.sops
       sops backend/.env.sops

     SOPS decrypts each file via AWS KMS, opens the plaintext in \$EDITOR,
     and re-encrypts on save. Never commit the plaintext sibling files —
     .gitignore keeps them out.

  2. Commit the encrypted files and the updated .sops.yaml:

       git add .sops.yaml infra/terraform.tfvars.sops backend/.env.sops
       git commit -m "ops: wire up SOPS + KMS for secrets"

  3. For local dev, decrypt backend/.env.sops whenever you need a fresh .env:

       sops -d backend/.env.sops > backend/.env

     The plaintext .env is gitignored and overrides the committed
     backend/.env.development. Local dev with Docker Compose works fine off
     the committed backend/.env.development alone — only deployed environments
     consume backend/.env.sops.

${C_BOLD}Daily workflow:${C_RESET}
  - Edit a secret:      sops <file>.sops
  - Rotate one value:   sops <file>.sops   (edit, save)
  - Add a collaborator: grant them kms:Decrypt on the KMS key (IAM policy),
                        no changes to .sops.yaml or re-encryption needed.

${C_BOLD}Recovery from laptop loss:${C_RESET}
  Your AWS login is the backup. On a new machine: install sops + awscli,
  clone the repo, run \`aws configure\`, and you can decrypt immediately.
  There is NO key file to back up.

${C_BOLD}KMS key details:${C_RESET}
  Region:  $KMS_REGION
  Alias:   $KMS_ALIAS_PATH
  ARN:     $KMS_ARN

EOF
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

main() {
	check_prereqs
	ensure_kms_key
	update_sops_config
	seed_encrypted_files
	print_next_steps
}

main "$@"
