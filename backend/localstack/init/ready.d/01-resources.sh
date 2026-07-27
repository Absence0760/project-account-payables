#!/usr/bin/env bash
# Provision the AWS resources the app expects, on LocalStack boot.
#
# LocalStack runs every executable in /etc/localstack/init/ready.d once the core
# is ready. `awslocal` is the bundled aws-cli pre-pointed at the local endpoint.
# Idempotent enough for dev: re-running just re-asserts the same resources.
#
# Mirrors the env the app should be given (see docs/local-aws-localstack.md):
#   FEOH_AWS_ENDPOINT_URL=http://localhost:4566
#   FEOH_SQS_EXTRACTION_QUEUE_URL=http://localhost:4566/000000000000/feoh-extraction
#   FEOH_SQS_ERP_QUEUE_URL=http://localhost:4566/000000000000/feoh-erp
#   FEOH_SQS_AUDIT_QUEUE_URL=http://localhost:4566/000000000000/feoh-audit
#   FEOH_AUDIT_SHIPPING_S3_BUCKET=feoh-audit-worm
#   FEOH_AUDIT_SHIPPING_CLOUDWATCH_GROUP=/ap/audit
set -euo pipefail

echo "[init] creating SQS queues..."
awslocal sqs create-queue --queue-name feoh-extraction >/dev/null
awslocal sqs create-queue --queue-name feoh-erp >/dev/null
awslocal sqs create-queue --queue-name feoh-audit >/dev/null

echo "[init] verifying SES sender identity (no-reply@localhost)..."
awslocal ses verify-email-identity --email-address no-reply@localhost >/dev/null

echo "[init] creating CloudWatch log group /ap/audit..."
awslocal logs create-log-group --log-group-name /ap/audit 2>/dev/null || true

echo "[init] creating object-lock S3 bucket feoh-audit-worm..."
# Object Lock can only be enabled at create time, and requires versioning.
awslocal s3api create-bucket --bucket feoh-audit-worm --object-lock-enabled-for-bucket >/dev/null 2>&1 || true
awslocal s3api put-bucket-versioning --bucket feoh-audit-worm \
  --versioning-configuration Status=Enabled >/dev/null 2>&1 || true

echo "[init] done. resources ready."
