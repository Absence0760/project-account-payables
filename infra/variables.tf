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

variable "invoice_files_bucket_name" {
  description = "S3 bucket that stores uploaded invoice PDFs / images. Must be globally unique; typically '<project>-invoices-<env>-<random>'."
  type        = string
}

variable "audit_logs_bucket_name" {
  description = "S3 bucket that receives shipped tenant audit-log rows. Retention runs under Object Lock Compliance mode for 7 years — this bucket name cannot be reused after retention starts."
  type        = string
}

variable "invoice_retention_days" {
  description = "Default S3 Object Lock retention (days) applied to new invoice-file objects. Governance mode — a suitably privileged IAM principal can still override for a legitimate business reason. 365d is the shortest period that covers a full tax cycle."
  type        = number
  default     = 365
}

variable "audit_retention_days" {
  description = "Default S3 Object Lock retention (days) for shipped audit-log objects. Compliance mode — not even the root account can shorten it during the lock period. 7 years (2555 days) matches the SOX / SOC 2 long-tail evidence window."
  type        = number
  default     = 2555
}

variable "access_logs_bucket_name" {
  description = "S3 bucket that aggregates server-access logs from the invoice + audit-log buckets. Required by AWS-0089 / SOC 2 CC7.2 — every data-bearing bucket must have access logging enabled."
  type        = string
}

variable "access_logs_retention_days" {
  description = "Lifecycle expiration (days) for objects in the access-logs bucket. 365d covers a full audit cycle without the storage cost of indefinite retention; access logs are signal-of-access, not the audit trail itself (that's the Object Lock bucket)."
  type        = number
  default     = 365
}
