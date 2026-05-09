# infra/

Terraform stacks for project-account-payables. Today this is a minimal scaffold that stands up:

- An S3 bucket for remote tfstate (`bootstrap/`)
- The GitHub Actions OIDC provider + per-env deploy roles (`github-oidc/`)
- Per-env sops KMS keys (`envs/{preview,prod}/`, via `modules/sops-kms`)

The runtime stack (where the app actually runs — Lambda / ECS / S3+CloudFront / etc.) is intentionally **not yet present**. It gets added as another module call inside `envs/<env>/main.tf` once the app's deployment target is decided.

For the operator scripts that wrap `terraform apply` + `sops` + `aws` flows, see [`bin/`](../bin/README.md).

## Layout

```
infra/
├── .sops.yaml              sops file routing — KMS ARN per env
├── bootstrap/              one-time: S3 state bucket
├── github-oidc/            OIDC provider + per-env deploy roles
├── modules/
│   └── sops-kms/           reusable per-env KMS key (used by envs/*)
└── envs/
    ├── preview/            instantiates sops-kms; runtime stack TBD
    └── prod/               instantiates sops-kms; runtime stack TBD
```

Each stack except `bootstrap/` has remote state in the bucket created by `bootstrap`. State locking is S3-native via `use_lockfile = true` (Terraform ≥ 1.10) — no DynamoDB table required.

**Region.** Everything sits in `us-east-1`. If you eventually deploy to CloudFront, the ACM cert has to live there anyway. To deploy somewhere else, change the default in every `aws_region` variable + every `region` field in the `backend.tf` files.

## First-time setup

> **Quick path:** the [`bin/`](../bin/) scripts wrap the AWS / sops / terraform sequences below. Read this section once so you know what they're doing.

### 0. Prereqs

- AWS account with an Identity Center user (or IAM admin) able to assume `AdministratorAccess`.
- Tooling on your workstation: `terraform >= 1.13`, `aws` CLI v2, `sops`, `jq`.
- Authenticate: `aws sso login --profile <your-profile>`. Set `AWS_PROFILE` for the shell.

Verify with `bin/aws-preflight.sh` before any `terraform apply`.

### 1. Bootstrap (one-time, local state)

```bash
cd infra/bootstrap
terraform init
terraform apply
```

Creates the `account-payables-tfstate` bucket every other stack uses for remote state. Local state only — never migrate this stack into the bucket it creates.

### 2. GitHub OIDC + deploy roles

```bash
cd ../github-oidc
terraform init
terraform apply -var "github_repo=<owner>/project-account-payables"
```

Creates the OIDC provider plus two deploy roles (`ap-deploy-prod`, `ap-deploy-preview`) with placeholder permissions. **Tighten the placeholder policies in `main.tf` before any real deploy ships** — never leave them at `sts:GetCallerIdentity` in production.

Save both role ARNs as GitHub repository secrets (`AWS_DEPLOY_ROLE_ARN_PROD`, `AWS_DEPLOY_ROLE_ARN_PREVIEW`) — workflow files reference them.

### 3. Per-env sops KMS (preview first)

```bash
cd ../envs/preview
cp terraform.tfvars.example terraform.tfvars   # fill in operator_role_arns
terraform init
terraform apply
```

Creates the preview KMS key with rotation enabled.

### 4. Wire sops to the new key

```bash
bin/sops-init.sh preview
```

The script:
1. Reads `kms_key_arn` from terraform output.
2. Replaces `REPLACE_PREVIEW_KMS_ARN` in `infra/.sops.yaml`.
3. Seeds `infra/envs/preview/secrets.enc.yaml` with a placeholder so subsequent `sops` opens have something to decrypt.

### 5. Set a real secret

```bash
echo -n "$DATABASE_URL" | bin/secret-set.sh preview DATABASE_URL
# or
sops infra/envs/preview/secrets.enc.yaml   # interactive editor
```

### 6. Repeat for prod

Same flow with `prod` instead of `preview`. The two KMS keys are independent — a leak of the preview key never decrypts prod secrets.

## Conventions

- **Idempotent.** `terraform apply` against an already-applied stack is a no-op.
- **Region pinned.** `us-east-1` everywhere. Every `aws_region` var and every backend `region` line agrees.
- **State bucket has `prevent_destroy = true`.** A stray `terraform destroy` on the bootstrap stack would orphan every other stack's state. The lifecycle block forces a manual `terraform state rm` first.
- **No long-lived AWS credentials in CI.** Deploy workflows assume the `ap-deploy-<env>` role via OIDC — short-lived STS tokens only. The IAM users that exist in this account are operator humans, not service accounts.
- **Secrets are sops-encrypted in git.** The encrypted file is committed; the *key* never leaves AWS. The runtime stack reads the file via the sops Terraform provider when it's added.
- **One KMS key per env.** Preview and prod never share a key. A compromise of one env's key reveals nothing about the other.

## Cost (idle)

- 2 KMS keys = $2/month
- S3 state bucket = pennies/month
- IAM roles = $0
- Total: **~$2/month** until the runtime stack lands.
