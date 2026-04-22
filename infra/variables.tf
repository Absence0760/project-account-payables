variable "aws_region" {
  description = "Primary AWS region for all resources defined in this module."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project slug used for resource naming + default_tags."
  type        = string
  default     = "account-payables"
}

variable "environment" {
  description = "Deployment environment (prod, staging, etc.). Becomes part of resource names and tags so multiple envs can coexist in the same account."
  type        = string
  default     = "prod"
}
