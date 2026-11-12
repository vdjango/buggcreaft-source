"""Independent, content-addressed Rainyun archive queue and conservative budgets."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "archive_objects",
        sa.Column("storage_id", sa.String(64), primary_key=True),
        sa.Column("object_key", sa.String(512), primary_key=True),
        sa.Column("local_key", sa.String(256), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("capacity_reserved", sa.Boolean(), nullable=False),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_archive_claim", "archive_objects", ["storage_id", "state", "created_at"])
    op.create_table(
        "archive_spaces",
        sa.Column("storage_id", sa.String(64), primary_key=True),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "upload_budgets",
        sa.Column("storage_id", sa.String(64), primary_key=True),
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("upload_budgets")
    op.drop_table("archive_spaces")
    op.drop_index("ix_archive_claim", table_name="archive_objects")
    op.drop_table("archive_objects")
