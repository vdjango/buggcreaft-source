"""Explicit artifact publication; scans imported through admin remain private."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("artifacts", "size", type_=sa.BigInteger(), existing_type=sa.Integer())
    op.create_table(
        "cached_artifacts",
        sa.Column("sha256", sa.String(64), sa.ForeignKey("artifacts.sha256"), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("public", sa.Boolean(), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("collection_jobs.id")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cached_artifacts")
