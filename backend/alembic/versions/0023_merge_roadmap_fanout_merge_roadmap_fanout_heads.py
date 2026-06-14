"""merge roadmap fanout heads

Revision ID: 0023_merge_roadmap_fanout
Revises: 0022_invoice_meta_summary, 0022_notifications, 0022_sox_audit_immutable,
    0022_vendor_change_requests
Create Date: 2026-06-11 17:41:53.116679

"""

revision = "0023_merge_roadmap_fanout"
down_revision = (
    "0022_invoice_meta_summary",
    "0022_notifications",
    "0022_sox_audit_immutable",
    "0022_vendor_change_requests",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
