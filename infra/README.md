# infra/

Infrastructure-as-code for this project. Currently scoped to **SOPS + KMS** plumbing — the shared secret-encryption infra. Real AWS resources (ECS, ALB, RDS, CloudFront) are not yet defined here; they live on the roadmap under `docs/production-deployment.md`.

## Layout

```
infra/
├── .gitignore                   # ignores .terraform/, *.tfstate, *.tfplan
├── terraform.tfvars.example     # committed template for future vars
├── terraform.tfvars.sops        # encrypted, committed (created by bin/sops-init.sh)
└── README.md                    # this file
```

When real Terraform resources arrive, they'll live directly in this folder (e.g. `main.tf`, `variables.tf`, `ecs.tf`, `rds.tf`) — matching the structure in the sibling `meryl-green-designs` repo.

## SOPS + KMS bootstrap

The SOPS KMS key is **not** provisioned by Terraform. It's created out-of-band by `bin/sops-init.sh` so the key exists before Terraform has a way to read its own secrets (chicken-and-egg avoidance). The script is idempotent: re-runs reuse the existing key.

```bash
./bin/sops-init.sh
```

That single command:
1. Checks prereqs (`sops`, `aws`, `jq`, authenticated AWS CLI)
2. Creates or discovers the KMS key + alias (`alias/account-payables-sops`)
3. Replaces placeholders in `.sops.yaml` with the real ARN
4. Seeds `infra/terraform.tfvars.sops` and `backend/.env.sops` from their `.example` siblings

After the script finishes, edit the two encrypted files with `sops <file>.sops` and commit the results.

## Editing secrets

```bash
sops infra/terraform.tfvars.sops      # decrypt → $EDITOR → re-encrypt on save
sops backend/.env.sops
```

## Adding a new operator

Grant the operator `kms:Decrypt` (and usually `kms:Encrypt`, `kms:GenerateDataKey`) on the KMS key via an IAM policy. No changes to `.sops.yaml` or re-encryption are needed — IAM is the source of truth.

## Tearing everything down

```bash
# Find the key ID behind the alias
aws kms describe-key --region us-east-1 --key-id alias/account-payables-sops \
  --query 'KeyMetadata.KeyId' --output text

# Schedule deletion (minimum 7-day pending window)
aws kms schedule-key-deletion --region us-east-1 \
  --key-id <KEY_ID> --pending-window-in-days 7
```

Cost of leaving the key in place: ~$1/month.

## See also

- `../.sops.yaml` — creation rules binding the encrypted files to this KMS key
- `../bin/sops-init.sh` — the bootstrap script
- `../backend/CLAUDE.md` § Secrets management — day-to-day encrypt/decrypt workflow
