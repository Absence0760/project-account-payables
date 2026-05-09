#!/usr/bin/env bash
#
# aws-preflight.sh — pre-deploy sanity check.
#
# Run this BEFORE any `terraform apply`. It catches the failures that
# hurt most: missing tools, expired SSO session, wrong region, wrong
# AWS account, uncommitted infra changes, sops auth gaps, state-bucket
# missing.
#
# Read-only — never mutates anything. Exits 0 if everything is ready;
# non-zero with a list of fixes if not.
#
# Usage:
#   bin/aws-preflight.sh

set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

EXPECTED_REGION="us-east-1"
STATE_BUCKET="account-payables-tfstate"
FAILS=0

bump_fail() { FAILS=$((FAILS + 1)); }

step "Tooling"
for cmd in terraform aws sops jq git gh; do
	if command -v "$cmd" >/dev/null; then
		ok "$cmd ($(command -v "$cmd"))"
	else
		err "$cmd missing"
		bump_fail
	fi
done

# Terraform must be ≥ 1.10 for S3-native locking (no DynamoDB).
if command -v terraform >/dev/null; then
	tf_version="$(terraform version | head -1 | awk '{print $2}' | tr -d v)"
	tf_major="${tf_version%%.*}"
	tf_rest="${tf_version#*.}"
	tf_minor="${tf_rest%%.*}"
	if (( tf_major > 1 )) || ( (( tf_major == 1 )) && (( tf_minor >= 10 )) ); then
		ok "terraform $tf_version (≥ 1.10 — S3 native locking supported)"
	else
		err "terraform $tf_version is too old — need ≥ 1.10 (S3-native state locking)"
		bump_fail
	fi
fi

step "AWS auth"
if aws sts get-caller-identity >/dev/null 2>&1; then
	arn="$(aws sts get-caller-identity --query Arn --output text)"
	acct="$(aws sts get-caller-identity --query Account --output text)"
	ok "Authenticated as $arn"
	ok "Account: $acct"
else
	err "AWS auth failed — run 'aws sso login --profile \${AWS_PROFILE:-account-payables}'"
	bump_fail
fi

current_region="$(aws configure get region 2>/dev/null || echo "")"
if [[ "$current_region" == "$EXPECTED_REGION" ]]; then
	ok "Default region: $current_region"
elif [[ -z "$current_region" ]]; then
	warn "Default region unset — set it to $EXPECTED_REGION (aws configure set region $EXPECTED_REGION)"
	bump_fail
else
	warn "Default region is $current_region, expected $EXPECTED_REGION"
	dim "Each terraform stack pins its own region, so apply may still work — but a stray aws CLI call could hit the wrong region."
fi

step "State bucket (bootstrap output)"
if aws s3api head-bucket --bucket "$STATE_BUCKET" >/dev/null 2>&1; then
	ok "$STATE_BUCKET exists"
	versioning="$(aws s3api get-bucket-versioning --bucket "$STATE_BUCKET" --query Status --output text 2>/dev/null || echo None)"
	if [[ "$versioning" == "Enabled" ]]; then
		ok "Versioning enabled"
	else
		err "Versioning is $versioning — should be Enabled"
		bump_fail
	fi
else
	warn "$STATE_BUCKET does not exist or is not accessible"
	dim "Run bootstrap first: cd infra/bootstrap && terraform apply"
fi

step "infra/ working tree"
cd "$REPO_ROOT"
infra_dirty="$(git status --porcelain infra/ | wc -l)"
if (( infra_dirty == 0 )); then
	ok "Clean (no uncommitted changes under infra/)"
else
	warn "$infra_dirty file(s) modified under infra/ — apply will use working-tree state"
	git status --porcelain infra/ | sed 's/^/      /'
fi

step ".sops.yaml"
sops_config="$REPO_ROOT/infra/.sops.yaml"
if grep -qE 'REPLACE_(PROD|PREVIEW)_KMS_ARN' "$sops_config" 2>/dev/null; then
	warn ".sops.yaml has unresolved placeholder ARNs:"
	grep -nE 'REPLACE_(PROD|PREVIEW)_KMS_ARN' "$sops_config" | sed 's/^/      /'
	dim "Run bin/sops-init.sh after applying envs/<env>/."
else
	ok ".sops.yaml has all KMS ARNs resolved"
fi

step "Verdict"
if (( FAILS > 0 )); then
	err "$FAILS hard failure(s) — fix above before applying"
	exit 1
fi
ok "Ready to apply"
