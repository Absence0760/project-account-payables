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
#   c. Switch the application config (`FEOH_S3_BUCKET`) to the new name
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

resource "aws_s3_bucket_logging" "invoice_files" {
  bucket        = aws_s3_bucket.invoice_files.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "invoice-files/"
}

# Object Lock doesn't stop a lifecycle rule from being defined or evaluated —
# AWS just defers deletion of any version still inside its own lock window.
# Without this, noncurrent versions accumulate forever once retention starts
# elapsing. The `+ 30` buffer keeps expiration from racing the lock itself.
resource "aws_s3_bucket_lifecycle_configuration" "invoice_files" {
  bucket = aws_s3_bucket.invoice_files.id

  rule {
    id     = "expire-noncurrent-invoice-file-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.invoice_retention_days + 30
    }
  }
}

# Defense-in-depth: explicitly deny any request over plain HTTP. Public
# Access Block already stops unauthenticated/public access, so this is
# hardening rather than closing an open hole.
data "aws_iam_policy_document" "invoice_files_tls" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.invoice_files.arn,
      "${aws_s3_bucket.invoice_files.arn}/*",
    ]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "invoice_files_tls" {
  bucket = aws_s3_bucket.invoice_files.id
  policy = data.aws_iam_policy_document.invoice_files_tls.json
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

resource "aws_s3_bucket_logging" "audit_logs" {
  bucket        = aws_s3_bucket.audit_logs.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "audit-logs/"
}

# Object Lock doesn't stop a lifecycle rule from being defined or evaluated —
# AWS just defers deletion of any version still inside its own lock window.
# Without this, noncurrent versions accumulate forever once retention starts
# elapsing. The `+ 30` buffer keeps expiration from racing the lock itself.
resource "aws_s3_bucket_lifecycle_configuration" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id

  rule {
    id     = "expire-noncurrent-audit-log-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.audit_retention_days + 30
    }
  }
}

# Defense-in-depth: explicitly deny any request over plain HTTP. Public
# Access Block already stops unauthenticated/public access, so this is
# hardening rather than closing an open hole.
data "aws_iam_policy_document" "audit_logs_tls" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.audit_logs.arn,
      "${aws_s3_bucket.audit_logs.arn}/*",
    ]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "audit_logs_tls" {
  bucket = aws_s3_bucket.audit_logs.id
  policy = data.aws_iam_policy_document.audit_logs_tls.json
}


# --- Server-access logs bucket ----------------------------------------------
# Sink for `aws_s3_bucket_logging` from the data-bearing buckets above. SOC 2
# CC7.2 (audit evidence of access) + AWS-0089 (Trivy IaC: "Bucket has logging
# disabled") both require it. The logs themselves are signal-of-access, not
# the system-of-record audit trail (that lives in the COMPLIANCE-mode bucket
# above), so we keep them at SSE-KMS + 365-day lifecycle expiry rather than
# Object Lock.
#
# IMPORTANT: this bucket MUST NOT have its own logging enabled — pointing a
# logging bucket at itself produces an infinite-loop of log objects (each
# write generates a log line that triggers another write). AWS rejects the
# config, but the safety check is to leave the aws_s3_bucket_logging
# resource off entirely.

# We intentionally do not enable access logging on this bucket — it IS
# the access-logs sink. Pointing it at itself creates an infinite
# log-write loop AWS rejects at apply time. The upstream Trivy rule
# has no exception for "logging-target" buckets, so we suppress it
# inline rather than per-resource. Trivy parses `#trivy:ignore:<id>`
# (no space after `#`, on its own line above the resource).
#trivy:ignore:AWS-0089
resource "aws_s3_bucket" "access_logs" {
  bucket = var.access_logs_bucket_name

  tags = {
    Name    = var.access_logs_bucket_name
    purpose = "s3-access-logs"
  }
}

resource "aws_s3_bucket_versioning" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.app.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 access-log delivery uses the legacy "log delivery group" canned ACL,
# which requires the bucket-owner-preferred ownership controls — without
# this the logging.target_bucket reference fails at apply time with
# "AccessControlListNotSupported".
resource "aws_s3_bucket_ownership_controls" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    id     = "expire-old-access-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = var.access_logs_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}
