"""Indexed cursor browsing without loading scan JSON or collection history."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_scans_browse", "scans", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_scans_browse", table_name="scans")
