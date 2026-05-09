output "deploy_role_arn_prod" {
  description = "Set as `AWS_DEPLOY_ROLE_ARN_PROD` in GitHub Secrets."
  value       = aws_iam_role.deploy_prod.arn
}

output "deploy_role_arn_preview" {
  description = "Set as `AWS_DEPLOY_ROLE_ARN_PREVIEW` in GitHub Secrets."
  value       = aws_iam_role.deploy_preview.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub Actions OIDC provider — referenced by any new deploy role you add later."
  value       = aws_iam_openid_connect_provider.github.arn
}
