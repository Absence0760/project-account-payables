output "app_kms_key_arn" {
  description = "ARN of the customer-managed KMS key used for at-rest encryption (RDS / S3 / SQS)."
  value       = aws_kms_key.app.arn
}

output "app_kms_key_alias" {
  description = "Alias name pointing at the app KMS key — safe to reference from IAM policies that want to survive key rotation."
  value       = aws_kms_alias.app.name
}
