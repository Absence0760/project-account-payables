# sops-kms — per-env KMS key used by sops to encrypt secrets at rest in
# git. The encrypted file (`infra/envs/<env>/secrets.enc.yaml`) is
# committed; the *key* never leaves AWS.
#
# Usage flow:
#   1. `terraform apply` here creates the key.
#   2. `bin/sops-init.sh <env>` reads the `kms_key_arn` output and writes
#      it into `infra/.sops.yaml`, then seeds an empty
#      `secrets.enc.yaml`.
#   3. Operators edit secrets with `sops infra/envs/<env>/secrets.enc.yaml`.
#   4. The runtime stack (added later) reads the encrypted file via the
#      sops Terraform provider and mounts the values into the app.
#
# Rotation: enabling automatic key rotation rotates the underlying key
# material transparently — no operator action needed. If the key itself
# is destroyed and recreated (e.g. during DR), `bin/key-rotate.sh
# <env>` re-encrypts the file under the new key.

data "aws_caller_identity" "current" {}

locals {
  base_tags = merge(
    {
      Project     = "account-payables"
      Stack       = "sops-kms"
      Environment = var.env
      ManagedBy   = "terraform"
    },
    var.tags,
  )
}

resource "aws_kms_key" "sops" {
  description             = "sops encryption key for account-payables/${var.env}"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid       = "EnableRootAccountAccess"
          Effect    = "Allow"
          Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
          Action    = "kms:*"
          Resource  = "*"
        },
      ],
      length(var.operator_role_arns) > 0 ? [
        {
          Sid       = "AllowOperatorEncryptDecrypt"
          Effect    = "Allow"
          Principal = { AWS = var.operator_role_arns }
          Action = [
            "kms:Encrypt",
            "kms:Decrypt",
            "kms:ReEncrypt*",
            "kms:GenerateDataKey*",
            "kms:DescribeKey",
          ]
          Resource = "*"
        },
      ] : [],
    )
  })

  tags = local.base_tags
}

resource "aws_kms_alias" "sops" {
  name          = "alias/account-payables-${var.env}-sops"
  target_key_id = aws_kms_key.sops.key_id
}
