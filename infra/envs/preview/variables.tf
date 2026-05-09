variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "operator_role_arns" {
  description = "IAM principals (humans + the preview deploy role from infra/github-oidc) that need to encrypt / decrypt the env's sops file. The account root is added automatically by the module."
  type        = list(string)
  default     = []
}
