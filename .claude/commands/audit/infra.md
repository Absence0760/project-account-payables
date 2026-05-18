---
description: Audit the Terraform stacks under infra/ — IAM least-privilege, encryption, drift hygiene, cost + DR guardrails
---

Audit the Terraform stacks at `infra/` against the architecture documented in `docs/architecture.md` and `docs/deployment.md`.

## Goal

The web app's blast radius runs through these stacks: a permissive OIDC trust policy makes the entire AWS account writable from a fork's PR; a public S3 bucket undoes the privacy story for the order data; a missing `lifecycle.ignore_changes` makes Terraform fight CI on every deploy. Catch the high-cost mistakes before `terraform apply` reaches a real account.

## Files in scope (the whole `infra/` directory)

All ten `.tf` files plus the encrypted tfvars + template:

- `main.tf` — root module, providers
- `variables.tf` — input variables, validation
- `outputs.tf` — exported values (the source of the GitHub Actions repo vars)
- `s3_cloudfront.tf` — static site hosting + distribution + ACM cert + Route 53 records
- `api_gateway.tf` — backend API surface
- `lambda.tf` — backend function + IAM role + log group (HTTP-only handler post-Phase-1; the EventBridge-dispatch branch that handled the old monthly PII sweep was removed at the Day 8 cutover)
- `dynamodb.tf` — orders table with per-item TTL (the `ttl` attribute drives PII retention now; there is no longer a `pii_cleanup.tf` — the file was deleted at Phase 1)
- `security_headers.tf` — CloudFront response-headers policy (CSP, HSTS, frame-options, etc.)
- `github_oidc.tf` — GitHub OIDC federation + deploy role
- `budget.tf` — AWS Budgets monthly cap + notifications
- `terraform.tfvars.sops` — encrypted variable values
- `terraform.tfvars.example` — committed template

## What to check

1. **State backend.** `infra/main.tf` declares `backend "s3"` with:
   - `bucket = "my-project-tfstate"`, `key = "prod/terraform.tfstate"`, `region = "<aws-region>"`
   - `encrypt = true`
   - Locking via `dynamodb_table = "my-project-tfstate-lock"` (the legacy DynamoDB pattern — `bin/setup.sh` bootstraps both bucket and lock table together).

   **Audit calls:**
   - Bucket exists + has versioning + has Public Access Block — confirm via the bootstrap script (not via TF since the bucket pre-exists the state). Flag if `bin/setup.sh` is missing any of these.
   - DynamoDB lock table exists and has `LockID` as its hash key.
   - Consider recommending migration to **S3-native locking** (`use_lockfile = true`, Terraform ≥ 1.10) as a future cleanup — drops the DynamoDB resource entirely, saves a few cents/month, simpler bootstrap. Not a current finding; surface as a Low / Note unless the user asks for the upgrade.

2. **OIDC trust policy (`github_oidc.tf`).**
   - `aws_iam_role.github_actions` has a trust policy (built by `data "aws_iam_policy_document" "github_actions_trust"`) with TWO `StringEquals` conditions:
     - `token.actions.githubusercontent.com:aud = "sts.amazonaws.com"`
     - `token.actions.githubusercontent.com:sub = "repo:${var.github_repo}:environment:production"`
   - **This is environment-scoped, not branch-scoped.** Release-gated deploys run with `github.ref = refs/tags/<tag>`, so a `ref:refs/heads/main` or `ref:refs/tags/*` subject pattern would reject the actual deploy events. Workflows must declare `environment: production` to assume the role; the environment is created by `bin/setup.sh` and carries the deploy-related repo vars.
   - If the `:sub` condition gets weakened to a wildcard (`StringLike` with `*`) or removed entirely, that's the canonical "fork PR can assume your role" footgun — Critical.
   - `aws_iam_openid_connect_provider.github` has `client_id_list = ["sts.amazonaws.com"]` and a non-empty `thumbprint_list`. The provider is a one-per-account resource; if another project in the same AWS account already created it, this file imports it rather than duplicating (see the header comment in `github_oidc.tf`).
   - The role's attached policies (`aws_iam_role_policy` / `aws_iam_role_policy_attachment`) are scoped per-resource: S3 actions limited to the project's bucket ARN (no `*`), Lambda actions limited to the project's function ARN, CloudFront limited to `CreateInvalidation` on the project distribution. **No `iam:*` / `sts:AssumeRole` / `secretsmanager:*` on the deploy role.**

3. **S3 buckets (`s3_cloudfront.tf`).** Every bucket:
   - `aws_s3_bucket_public_access_block` with all four flags `true`
   - `aws_s3_bucket_versioning` enabled
   - `aws_s3_bucket_server_side_encryption_configuration` set
   - `aws_s3_bucket_policy` grants `Principal: { Service = "cloudfront.amazonaws.com" }` ONLY (not `Principal: "*"`) and conditions on `AWS:SourceArn`
   - No legacy `aws_s3_bucket_acl` (modern API forbids ACLs)
   - A lifecycle rule expiring non-current versions (cost guardrail)

4. **CloudFront distribution (`s3_cloudfront.tf` + `security_headers.tf`).** Every behavior:
   - `viewer_protocol_policy = "redirect-to-https"` (default) or `"https-only"` (origin behaviors)
   - `minimum_protocol_version = "TLSv1.2_2021"` or stricter
   - `origin_access_control_id` on the S3 origin (not the legacy `origin_access_identity`)
   - `response_headers_policy_id` attached to BOTH default and ordered behaviors
   - The response-headers policy in `security_headers.tf` has `strict_transport_security` (max_age ≥ 1 year, `include_subdomains`, `preload`), `content_type_options` (nosniff), `referrer_policy` (`strict-origin-when-cross-origin`), and `frame_options = "DENY"`.
   - **CSP is intentionally absent** — the file's header comment documents this as a deliberate choice ("the static site loads first-party JS plus Google Fonts and <CMS> images; defining a watertight CSP for that without breaking things is more work than it's worth at this scale"). **Don't flag CSP-absent as a finding** unless the project has started accepting user-generated content (e.g. comments on products) — at that point CSP becomes worth the work. If you find the rationale comment is gone but CSP is still missing, that's a Low.
   - `price_class = "PriceClass_100"` or `PriceClass_200` (not `PriceClass_All` unless documented).
   - SPA fallback `custom_error_response` rewrites 404/403 → 200 + `/404.html` (per `frontend/CLAUDE.md` § SPA fallback). Don't break this — `/shop/[slug]` depends on it.

5. **Lambda function (`lambda.tf`).**
   - `runtime = "nodejs22.x"` (matches the root `package.json` engines).
   - `architectures = ["arm64"]` (Graviton is cheaper for the same code).
   - `memory_size` + `timeout` reasonable.
   - The execution role has only `AWSLambdaBasicExecutionRole` attached unless extra perms are documented.
   - `aws_cloudwatch_log_group` for the Lambda has `retention_in_days` set (default infinite is a cost trap; recommend 30 or 90).
   - If a `lifecycle.ignore_changes` exists, the list is **minimal** — anything beyond `filename`/`source_code_hash` is suspicious.

6. **PII retention on the DynamoDB orders table (`dynamodb.tf`).**
   - The table has a `ttl` block: `attribute_name = "ttl"`, `enabled = true`. Matches `docs/security.md § PII retention`.
   - The `ttl` attribute on each row is set to `createdAt + 365 days` (Unix seconds) by `backend/src/orders-store.ts:buildPiiItem`. Verify the math hasn't drifted.
   - The pre-Phase-1 `pii_cleanup.tf` (EventBridge schedule + Lambda permission) was deleted at the Day 8 cutover. If you see that file, the Phase 1 migration is incomplete — flag.
   - Same for the Lambda's old scheduled-event dispatcher branch in `backend/src/lambda.ts` — the Phase 1 handler is HTTP-only.

7. **API Gateway (`api_gateway.tf`).**
   - HTTP API (not REST API — cheaper, no edge cases we need).
   - CORS: `allow_origins` matches `frontend/.env.example`'s `PUBLIC_API_URL` host (the production frontend domain). No `*` wildcard. No localhost in prod.
   - No `route_settings` with unbounded throttling — at least set `throttling_burst_limit` and `throttling_rate_limit` on `$default` to a sane value (per-second 100ish; per-IP rate limits already exist in `backend/src/rate-limit.ts`, but API Gateway is the global cap).

8. **KMS keys.** The SOPS key (`alias/my-project-sops` in `<aws-region>`) is provisioned out-of-band by `bin/sops-init.sh`, not by this Terraform module. Confirm no `.tf` file tries to manage it (would conflict). DynamoDB SSE-KMS, if added, uses the AWS-managed `aws/dynamodb` key, not the SOPS CMK (per `docs/orders-pii-split-plan.md`).

9. **Secrets handling.**
   - `infra/terraform.tfvars.sops`: encrypted (verify `sops:` block at the end + ENC[...] values). Plaintext sibling `infra/terraform.tfvars` is gitignored.
   - Variables holding secrets marked `sensitive = true`.
   - No `output` exposes a sensitive value without `sensitive = true`.

10. **Provider + Terraform pinning.** Every stack has a `versions.tf` (or equivalent) with `required_version = ">= 1.13"` (or current) and pinned `required_providers`. Provider versions use `~> X.Y` or exact pins. `.terraform.lock.hcl` should be committed.

11. **Budget guardrails (`budget.tf`).**
   - `aws_budgets_budget` resource exists with a sane `monthly_budget_limit_usd` (the documented baseline is single-digit dollars/month given the traffic level).
   - Three notifications minimum: `ACTUAL > 50 %`, `ACTUAL > 100 %`, `FORECASTED > 100 %`.
   - `budget_alert_emails` non-empty (a budget with zero subscribers is a no-op).

12. **Drift hygiene.** Read every `lifecycle { ignore_changes = [...] }` block — list each one and confirm:
    - It's there because CI legitimately mutates the field.
    - The list is minimal. Anything else is silent-drift-enabling.

13. **Tagging.** Every resource that supports `tags` has them, and the tag set includes at minimum `project = "my-project"`, `managed_by = "terraform"`. Tag-based cost attribution depends on this.

14. **No CloudFront / WAF gaps.** The site is static; the realistic attack is volumetric (denial-of-wallet via CloudFront egress + Lambda invocations from a botnet hitting `POST /orders`). Backend already has per-IP rate limiting; API Gateway throttling is the global cap. If a WAF is provisioned anywhere, confirm it's attached to the distribution; if not, flag as a Low (the project is small enough that WAF is over-engineering, but worth surfacing).

## Report

- **Critical** — OIDC trust policy too broad, public S3 bucket without OAC, plaintext secrets file committed, IAM role with `*` permissions.
- **High** — bucket versioning off, missing PAB on a bucket, CloudFront serving non-HTTPS, Lambda runtime deprecated (nodejs18.x or older), role permissions overscoped (`*` instead of specific ARNs), `terraform.tfvars` not gitignored, no AWS budget.
- **Medium** — log retention infinite, missing security headers, weak CSP, missing tags, drift-prone resource (no `ignore_changes` on a CI-mutated field), CloudFront `PriceClass_All` without justification.
- **Low** — version pin loose (no `~>`), undocumented `lifecycle` choice, missing `sensitive = true` on a borderline value, no WAF.

For each finding: file:line + the concrete change to make. Don't apply fixes without explicit confirmation.

## Useful starting points

- `infra/README.md` — the apply-order walkthrough
- `infra/CLAUDE.md` — per-workspace conventions
- `infra/github_oidc.tf` — the highest blast-radius file
- `infra/s3_cloudfront.tf` — site hosting + CDN
- `infra/lambda.tf` + `infra/api_gateway.tf` — backend surface
- `infra/security_headers.tf` — CSP + HSTS + friends
- `docs/deployment.md` — what the architecture is supposed to look like; finding deltas against that doc IS a finding
- `docs/security.md` — risk register
- `docs/architecture.md` — system diagram

## Delegate to

`general-purpose` agent with this file as the prompt body. The audit reads ~10 small `.tf` files plus checks 2–3 conditions per file, well within one agent's reading window.

Read-only. Findings only. Don't run `terraform plan` or `terraform apply` — those reach AWS.
