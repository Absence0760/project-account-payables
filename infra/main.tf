# Root Terraform entrypoint.
#
# The AWS stack is still being built out — see docs/production-deployment.md
# for the planned shape (ECS, ALB, CloudFront, RDS). What lives here today is
# the security substrate needed as a SOC 2 engineering prerequisite:
#
#   - kms.tf  : customer-managed KMS key for at-rest encryption, auto-rotated
#   - s3.tf   : buckets for invoice files and audit-log shipping, with
#               versioning + Object Lock
#
# The `terraform` block pins versions and declares an S3 remote state backend.
# Backend config values live in terraform.tfvars.sops (SOPS-encrypted) so the
# root module can be re-initialised without each operator typing the bucket
# name by hand.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Backend config is intentionally empty here and supplied via `terraform
  # init -backend-config=…`. Storing backend config in-file makes it hard
  # to switch environments; a partial-backend pattern is the canonical
  # Hashicorp recommendation.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project     = var.project
      managed_by  = "terraform"
      environment = var.environment
    }
  }
}
