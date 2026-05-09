#!/usr/bin/env bash
#
# aws-login.sh — convenience wrapper for `aws sso login`. Reads the
# profile from $AWS_PROFILE (or the first argument), opens a browser
# tab to authenticate, and verifies the session afterwards.
#
# Usage:
#   bin/aws-login.sh                       # uses $AWS_PROFILE
#   bin/aws-login.sh account-payables      # explicit profile

set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROFILE="${1:-${AWS_PROFILE:-account-payables}}"

need_cmd aws

step "Logging in to AWS SSO (profile: $PROFILE)"
aws sso login --profile "$PROFILE"

step "Verifying session"
arn="$(aws sts get-caller-identity --profile "$PROFILE" --query Arn --output text)"
acct="$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)"
ok "Authenticated as $arn"
ok "Account: $acct"

if [[ "${AWS_PROFILE:-}" != "$PROFILE" ]]; then
	log ""
	dim "Tip: 'export AWS_PROFILE=$PROFILE' to make this profile sticky for the shell."
fi
