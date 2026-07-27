#!/usr/bin/env bash
# Provision the AWS resources the app expects, on LocalStack boot.
#
# LocalStack runs every executable in /etc/localstack/init/ready.d once the core
# is ready. `awslocal` is the bundled aws-cli pre-pointed at the local endpoint.
# Idempotent enough for dev: re-running just re-asserts the same resources.
#
# Mirrors the env the app should be given (see docs/local-aws-localstack.md):
#   FEOH_AWS_ENDPOINT_URL=http://localhost:4566
#   FEOH_SQS_EXTRACTION_QUEUE_URL=http://localhost:4566/000000000000/ap-extraction
#   FEOH_SQS_ERP_QUEUE_URL=http://localhost:4566/000000000000/ap-erp
#   FEOH_SQS_AUDIT_QUEUE_URL=http://localhost:4566/000000000000/ap-audit
#   FEOH_AUDIT_SHIPPING_S3_BUCKET=ap-audit-worm
#   FEOH_AUDIT_SHIPPING_CLOUDWATCH_GROUP=/ap/audit
set -euo pipefail

echo "[init] creating SQS queues..."
awslocal sqs create-queue --queue-name ap-extraction >/dev/null
awslocal sqs create-queue --queue-name ap-erp >/dev/null
awslocal sqs create-queue --queue-name ap-audit >/dev/null

echo "[init] verifying SES sender identity (no-reply@localhost)..."
awslocal ses verify-email-identity --email-address no-reply@localhost >/dev/null

echo "[init] creating CloudWatch log group /ap/audit..."
awslocal logs create-log-group --log-group-name /ap/audit 2>/dev/null || true

echo "[init] creating object-lock S3 bucket ap-audit-worm..."
# Object Lock can only be enabled at create time, and requires versioning.
awslocal s3api create-bucket --bucket ap-audit-worm --object-lock-enabled-for-bucket >/dev/null 2>&1 || true
awslocal s3api put-bucket-versioning --bucket ap-audit-worm \
  --versioning-configuration Status=Enabled >/dev/null 2>&1 || true

echo "[init] done. resources ready."
