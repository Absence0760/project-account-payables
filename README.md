# project-account-payables

An accounts-payable system. The application stack is undecided as of this commit; what's checked in today is the scaffold the app will land on top of.

## What's here

- **`.claude/`** — review agents and slash commands (`/check`, `/safe-edit`).
- **`.github/`** — CI (terraform validate + shellcheck), Claude Code action, dependabot.
- **`bin/`** — operator scripts: `aws-login`, `aws-preflight`, `sops-init`, `secret-set`, `key-rotate`. See [`bin/README.md`](bin/README.md).
- **`infra/`** — Terraform: state bucket bootstrap, GitHub Actions OIDC + deploy roles, per-env sops KMS keys. See [`infra/README.md`](infra/README.md).
- **`tests-e2e/`** — Playwright scaffold (config, fixtures, one example spec). See [`tests-e2e/README.md`](tests-e2e/README.md).
- **`CLAUDE.md`** — orientation for AI sessions. House rules, project invariants, what to update when the app stack lands.

## First-time setup (infra path)

```bash
# 1. Authenticate to AWS via SSO
bin/aws-login.sh

# 2. Stand up the Terraform state bucket (one-time, local state)
cd infra/bootstrap && terraform init && terraform apply && cd -

# 3. Stand up the GitHub OIDC provider + deploy roles
cd infra/github-oidc && terraform init && terraform apply -var "github_repo=<owner>/project-account-payables" && cd -

# 4. Stand up the preview env's KMS key
cd infra/envs/preview && cp terraform.tfvars.example terraform.tfvars   # edit operator_role_arns
terraform init && terraform apply && cd -

# 5. Wire sops to the new key + seed an empty secrets file
bin/sops-init.sh preview

# 6. Verify the round-trip
sops -d infra/envs/preview/secrets.enc.yaml
```

Repeat steps 4–6 with `prod` instead of `preview` once you're ready.

## Adding the app

Once you commit to a stack, see [`CLAUDE.md` § "When the app stack lands"](CLAUDE.md#when-the-app-stack-lands) for the punchlist.
