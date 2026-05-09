# S3 buckets used by the backend.
#
# Every bucket defined in this module is configured with the four SOC 2
# baseline controls (docs/soc2-readiness.md § Encryption + Backup/Recovery):
#
#   1. Server-side encryption (SSE-KMS, customer-managed key from kms.tf)
#   2. Versioning enabled (required for Object Lock, also gives us a safety
#      net against accidental overwrites / deletes)
#   3. Public access blocked at the bucket level
#   4. Object Lock enabled with a default retention rule
#
# IMPORTANT: `object_lock_enabled` on an `aws_s3_bucket` is immutable — once
# the bucket exists, you cannot turn Object Lock on. Migration path for
# buckets created before this change:
#   a. Create a new bucket (name + `-locked` suffix) with the lock config
#   b. Run `aws s3 sync` from the old bucket to the new one
#   c. Switch the application config (`AP_S3_BUCKET`) to the new name
#   d. Schedule deletion of the old bucket once retention on the new one is
#      verified and the app has cut over
#
# We DO NOT re-enable lock on the existing buckets here — doing so would
# destroy + recreate them and lose all historical invoice files. The
# Terraform below is for the net-new buckets; the caveat above documents
# the migration for anything that predates this PR.


# --- Invoice files bucket ----------------------------------------------------
# Stores PDFs and images uploaded from the portal / email ingest. Governance-
# mode Object Lock means an admin with `s3:BypassGovernanceRetention` can
# still override for a valid business reason (GDPR right-to-erasure, legal
# hold release). 365 days covers a full tax cycle.

resource "aws_s3_bucket" "invoice_files" {
  bucket              = var.invoice_files_bucket_name
  object_lock_enabled = true

  tags = {
    Name    = var.invoice_files_bucket_name
    purpose = "invoice-files"
  }
}

resource "aws_s3_bucket_versioning" "invoice_files" {
  bucket = aws_s3_bucket.invoice_files.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "invoice_files" {
  bucket = aws_s3_bucket.invoice_files.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.app.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_object_lock_configuration" "invoice_files" {
  bucket = aws_s3_bucket.invoice_files.id

  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = var.invoice_retention_days
    }
  }
}

resource "aws_s3_bucket_public_access_block" "invoice_files" {
  bucket                  = aws_s3_bucket.invoice_files.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}


# --- Audit-log shipping bucket ----------------------------------------------
# Receives rows exported from each tenant's `audit_log` table. Compliance
# mode Object Lock means the retention period cannot be shortened by ANY
# principal — including the root account — during the lock window.
# Seven years (2555 days) matches the SOX / SOC 2 long-tail evidence window
# that Type II auditors ask about.

resource "aws_s3_bucket" "audit_logs" {
  bucket              = var.audit_logs_bucket_name
  object_lock_enabled = true

  tags = {
    Name    = var.audit_logs_bucket_name
    purpose = "audit-logs"
  }
}

resource "aws_s3_bucket_versioning" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.app.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_object_lock_configuration" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id

  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.audit_retention_days
    }
  }
}

resource "aws_s3_bucket_public_access_block" "audit_logs" {
  bucket                  = aws_s3_bucket.audit_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
