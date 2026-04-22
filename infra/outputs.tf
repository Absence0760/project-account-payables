output "app_kms_key_arn" {
  description = "ARN of the customer-managed KMS key used for at-rest encryption (RDS / S3 / SQS)."
  value       = aws_kms_key.app.arn
}

output "app_kms_key_alias" {
  description = "Alias name pointing at the app KMS key — safe to reference from IAM policies that want to survive key rotation."
  value       = aws_kms_alias.app.name
}

output "invoice_files_bucket" {
  description = "Name of the S3 bucket that stores uploaded invoice files."
  value       = aws_s3_bucket.invoice_files.bucket
}

output "audit_logs_bucket" {
  description = "Name of the S3 bucket that receives shipped audit-log objects. Subject to COMPLIANCE-mode Object Lock — deletion of this bucket is not possible until all objects have aged past their retention date."
  value       = aws_s3_bucket.audit_logs.bucket
}
