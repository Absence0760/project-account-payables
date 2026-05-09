output "state_bucket" {
  description = "Bucket name to use in other stacks' backend.tf."
  value       = aws_s3_bucket.state.id
}

output "region" {
  description = "Region the state bucket lives in. Other stacks must use the same value."
  value       = var.aws_region
}
