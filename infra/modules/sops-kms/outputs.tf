output "kms_key_arn" {
  description = "ARN of the per-env sops KMS key. Wire into infra/.sops.yaml via bin/sops-init.sh."
  value       = aws_kms_key.sops.arn
}

output "kms_key_id" {
  description = "Key id (UUID) for the per-env sops KMS key."
  value       = aws_kms_key.sops.key_id
}

output "kms_alias" {
  description = "Human-readable alias pointing at the key (alias/account-payables-<env>-sops)."
  value       = aws_kms_alias.sops.name
}
