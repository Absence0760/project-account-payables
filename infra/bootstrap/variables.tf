variable "aws_region" {
  description = "Region for the Terraform state bucket. All other stacks must use the same value."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "Name of the S3 bucket holding remote tfstate for every other stack. Locking is S3-native (use_lockfile = true) — no DynamoDB table needed since Terraform 1.10. Must be globally unique; convention is '<project>-tfstate'."
  type        = string
  default     = "account-payables-tfstate"
}
