"""KYC/AML compliance: vendor KYC status + sanctions check audit table.

Revision ID: 0018_kyc_compliance
Revises: 0017_international_payments
Create Date: 2026-05-10

Tenant DB only. Two changes:

  1. ``vendors`` gains four nullable columns to track KYC state:
       - ``kyc_status``: enum string — pending / verified / rejected /
         not_required. Default 'not_required' so existing vendors
         aren't gated retroactively (the orchestrator only refuses a
         payment when the corridor *demands* KYC).
       - ``kyc_verified_at``: when the most recent verification
         landed; null when status != 'verified'.
       - ``kyc_verified_by``: actor that signed off (a user_id from
         the control plane).
       - ``beneficial_owner_data``: JSONB blob for the documents +
         ownership tree. Free-form because each KYC provider returns
         a different shape; the orchestrator reads only the keys it
         cares about (high-risk-country flag, sanctioned-PEP flag).

  2. New ``sanctions_checks`` table — append-only audit log of every
     sanctions screening call. Each row: vendor_id, provider, the
     check_type ('initial', 'periodic', 'pre_payment'), the result
     ('clear', 'match', 'review_required'), provider-supplied risk
     score (nullable), the raw response JSONB, and the timestamp.
     Append-only means we never delete — auditors trace every
     decision back to the screening that justified it.
"""

from sqlalchemy import text

from alembic import op

revision = "0018_kyc_compliance"
down_revision = "0017_international_payments"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'vendors'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        """
        ALTER TABLE vendors
            ADD COLUMN IF NOT EXISTS kyc_status VARCHAR(20)
                NOT NULL DEFAULT 'not_required',
            ADD COLUMN IF NOT EXISTS kyc_verified_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS kyc_verified_by UUID,
            ADD COLUMN IF NOT EXISTS beneficial_owner_data JSONB
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sanctions_checks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            provider VARCHAR(50) NOT NULL,
            check_type VARCHAR(30) NOT NULL,
            result VARCHAR(30) NOT NULL,
            risk_score NUMERIC(5, 2),
            matched_list VARCHAR(80),
            raw_response JSONB,
            checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            correlation_id UUID
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sanctions_checks_vendor_id "
        "ON sanctions_checks (vendor_id, checked_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sanctions_checks_result "
        "ON sanctions_checks (result) WHERE result IN ('match', 'review_required')"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_sanctions_checks_result")
    op.execute("DROP INDEX IF EXISTS ix_sanctions_checks_vendor_id")
    op.execute("DROP TABLE IF EXISTS sanctions_checks")
    op.execute(
        """
        ALTER TABLE vendors
            DROP COLUMN IF EXISTS beneficial_owner_data,
            DROP COLUMN IF EXISTS kyc_verified_by,
            DROP COLUMN IF EXISTS kyc_verified_at,
            DROP COLUMN IF EXISTS kyc_status
        """
    )
