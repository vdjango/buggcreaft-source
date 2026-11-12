"""Durable dependency intake and worker pause control.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "collection_gate",
        sa.Column("paused", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "release_observations",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("collection_jobs.id"), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("release_observations")
    op.drop_column("collection_gate", "paused")
