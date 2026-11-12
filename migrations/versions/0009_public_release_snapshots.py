"""Persistent public publisher metadata snapshots.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "public_release_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("algorithm", sa.String(16), nullable=False),
        sa.Column("digest", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_public_release_identity",
        "public_release_snapshots",
        ["provider", "project_id", "release_id"],
    )


def downgrade():
    op.drop_index("ix_public_release_identity", table_name="public_release_snapshots")
    op.drop_table("public_release_snapshots")
