output "kms_key_arn" {
  description = "Used by bin/sops-init.sh to fill in infra/.sops.yaml."
  value       = module.sops_kms.kms_key_arn
}

output "kms_alias" {
  description = "Human-readable alias for the preview sops key."
  value       = module.sops_kms.kms_alias
}
