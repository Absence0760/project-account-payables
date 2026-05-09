# Preview environment.
#
# Today this stack only stands up the per-env sops KMS key. The runtime
# stack (Lambda / ECS / S3+CloudFront / whatever the app lands on) gets
# added as a sibling module call once the app's deployment target is
# decided. At that point the runtime module reads the sops-encrypted
# secrets file via the sops Terraform provider and wires the values
# into the running service.

provider "aws" {
  region = var.aws_region
}

module "sops_kms" {
  source             = "../../modules/sops-kms"
  env                = "preview"
  operator_role_arns = var.operator_role_arns
}
