# Customer-managed KMS key used for at-rest encryption of:
#   - RDS (backend DB)
#   - S3 objects in the invoice-files and audit-log buckets
#   - SQS queues that carry audit events
#
# SOPS uses a separate KMS key provisioned by `bin/sops-init.sh` out-of-band
# (chicken-and-egg: Terraform can't read its own encrypted tfvars before the
# key exists). That script flips `key_rotation_enabled` on at creation too —
# any new key spun up in-repo follows the same rule via this resource.
#
# `enable_key_rotation = true` is a SOC 2 engineering prereq
# (docs/soc2-readiness.md § Secrets management). Rotation is automatic and
# annual; AWS keeps the older key material around for decrypt, so nothing
# re-encrypts under the new material — but new encrypt operations use it.

resource "aws_kms_key" "app" {
  description             = "At-rest encryption for ${var.project} application data (RDS, S3, SQS)."
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name = "${var.project}-app-${var.environment}"
  }
}

resource "aws_kms_alias" "app" {
  name          = "alias/${var.project}-app-${var.environment}"
  target_key_id = aws_kms_key.app.key_id
}
