terraform {
  backend "s3" {
    bucket       = "account-payables-tfstate"
    key          = "github-oidc/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
