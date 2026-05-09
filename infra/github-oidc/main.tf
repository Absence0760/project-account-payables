# GitHub OIDC provider + per-env deploy roles.
#
# GitHub Actions assume these roles via OIDC token exchange — no long-
# lived AWS keys in `Settings → Secrets`. The trust policies are
# scoped per env:
#
#   prod    only assumable from a tag matching refs/tags/release@*
#   preview only assumable from a push to refs/heads/main
#
# `pull_request` events have a different `:sub` shape and CANNOT
# assume either role — that's correct: a fork PR must never be able to
# deploy. `workflow_dispatch` from `main` shares the `refs/heads/main`
# shape, so a manual run from `main` would assume deploy_preview;
# revisit if the deploy workflow ever picks up additional triggers.
#
# Permissions: each role's policy is intentionally a placeholder until
# the runtime stack is decided. When the app's deploy target lands
# (Lambda, ECS, S3+CloudFront, ECS+RDS), replace the empty
# `deploy_permissions` policy with one that scopes to the actual
# resource ARNs the deploy needs to mutate. Keep `Resource: "*"` out of
# anything finer-grained than `cloudfront:CreateInvalidation` (which
# AWS doesn't support resource-level matching on).

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  oidc_tags = merge(
    {
      Project   = "account-payables"
      Stack     = "github-oidc"
      ManagedBy = "terraform"
    },
    var.tags,
  )
}

# ─────────────────── OIDC provider ───────────────────

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
  tags = local.oidc_tags
}

# ─────────────────── Deploy role: prod ───────────────────

resource "aws_iam_role" "deploy_prod" {
  name = "ap-deploy-prod"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/tags/release@*"
        }
      }
    }]
  })
  tags = merge(local.oidc_tags, { Environment = "prod" })
}

resource "aws_iam_role_policy" "deploy_prod" {
  role = aws_iam_role.deploy_prod.id
  name = "deploy-permissions"
  # Placeholder. Tighten once the runtime stack lands — never leave
  # "Action: *, Resource: *" in production.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "NoOp"
      Effect   = "Allow"
      Action   = "sts:GetCallerIdentity"
      Resource = "*"
    }]
  })
}

# ─────────────────── Deploy role: preview ───────────────────

resource "aws_iam_role" "deploy_preview" {
  name = "ap-deploy-preview"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/main"
        }
      }
    }]
  })
  tags = merge(local.oidc_tags, { Environment = "preview" })
}

resource "aws_iam_role_policy" "deploy_preview" {
  role = aws_iam_role.deploy_preview.id
  name = "deploy-permissions"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "NoOp"
      Effect   = "Allow"
      Action   = "sts:GetCallerIdentity"
      Resource = "*"
    }]
  })
}
