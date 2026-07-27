# infra/

Infrastructure-as-code for this project. Scoped today to the **security substrate** needed as a SOC 2 engineering prerequisite (see `../docs/soc2-readiness.md`). Real AWS workload resources (ECS, ALB, RDS, CloudFront) are not yet defined here; they live on the roadmap under `docs/production-deployment.md`.

## Layout

```
infra/
├── .gitignore                   # ignores .terraform/, *.tfstate, *.tfplan
├── .terraform.lock.hcl          # committed — pins provider versions
├── main.tf                      # provider + backend + default_tags
├── variables.tf                 # aws_region, project, environment, bucket names, retention
├── kms.tf                       # customer-managed KMS key (rotation ON)
├── s3.tf                        # invoice-files + audit-logs buckets (versioning + Object Lock)
├── outputs.tf                   # exports for downstream modules
├── terraform.tfvars.example     # committed template
├── terraform.tfvars.sops        # encrypted, committed (created by bin/sops-init.sh)
└── README.md                    # this file
```

## Security posture

Every resource in this module follows the SOC 2 baseline:

| Control | Where |
|---|---|
| KMS auto-rotation (annual, customer-managed key) | `kms.tf` — `enable_key_rotation = true` |
| S3 versioning on every bucket | `s3.tf` — `aws_s3_bucket_versioning` = Enabled |
| S3 Object Lock — governance mode, 365d | `s3.tf` — invoice-files bucket |
| S3 Object Lock — compliance mode, 7y | `s3.tf` — audit-logs bucket |
| SSE-KMS on every bucket | `s3.tf` — references `aws_kms_key.app` |
| Public access block on every bucket | `s3.tf` — all four flags true |

### Caveat: Object Lock is immutable

`object_lock_enabled` on `aws_s3_bucket` is **set at creation and cannot be toggled afterwards**. The buckets defined in `s3.tf` are net-new. For any pre-existing bucket (e.g. one that predated this module):

1. Create a new bucket with `object_lock_enabled = true` (add a `-locked` suffix to avoid the name cooldown).
2. `aws s3 sync s3://old s3://new` to copy all objects across.
3. Update the application's `FEOH_S3_BUCKET` (or equivalent) to the new name and deploy.
4. Once retention on the new bucket is verified, schedule deletion of the old bucket.

This migration path is also tracked under "Pending — needs a code change" in `../docs/soc2-readiness.md`.

## Local usage

```bash
cd infra
terraform init -backend=false    # skip the S3 backend for local validation
terraform fmt -recursive .       # format
terraform validate               # syntactic + type-check (no AWS creds needed)
```

Real `apply` / `plan` runs target the S3 backend; pass the bucket + DynamoDB
table via `terraform init -backend-config=…` once they exist.

## SOPS + KMS bootstrap

The SOPS KMS key is **not** provisioned by the Terraform module above. It's created out-of-band by `bin/sops-init.sh` so the key exists before Terraform has a way to read its own secrets (chicken-and-egg avoidance). The script is idempotent: re-runs reuse the existing key.

```bash
./bin/sops-init.sh
```

That single command:
1. Checks prereqs (`sops`, `aws`, `jq`, authenticated AWS CLI)
2. Creates or discovers the KMS key + alias (`alias/feohledger-sops`)
3. Replaces placeholders in `.sops.yaml` with the real ARN
4. Seeds `infra/terraform.tfvars.sops` and `backend/.env.sops` from their `.example` siblings

After the script finishes, edit the two encrypted files with `sops <file>.sops` and commit the results.

Rotation for the SOPS key is documented in `../docs/secrets-rotation.md` — the key created by `sops-init.sh` is enrolled in AWS's annual auto-rotation at creation time, same as the app KMS key in `kms.tf`.

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
aws kms describe-key --region us-east-1 --key-id alias/feohledger-sops \
  --query 'KeyMetadata.KeyId' --output text

# Schedule deletion (minimum 7-day pending window)
aws kms schedule-key-deletion --region us-east-1 \
  --key-id <KEY_ID> --pending-window-in-days 7
```

Cost of leaving the key in place: ~$1/month.

Note: audit-logs bucket uses Object Lock in **Compliance** mode — you cannot delete that bucket until every object has aged past its 7-year retention. Factor that into any teardown plan.

## See also

- `../.sops.yaml` — creation rules binding the encrypted files to this KMS key
- `../bin/sops-init.sh` — the bootstrap script
- `../backend/CLAUDE.md` § Secrets management — day-to-day encrypt/decrypt workflow
- `../docs/soc2-readiness.md` — control-by-control status + pending items
