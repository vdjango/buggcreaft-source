"""Persistent shader metadata review queue.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shader_reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "observation_key",
            sa.String(64),
            sa.ForeignKey("release_observations.key"),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "shader_review_games",
        sa.Column("review_id", sa.String(64), sa.ForeignKey("shader_reviews.id"), primary_key=True),
        sa.Column("game_version", sa.String(128), primary_key=True),
    )
    op.create_index("ix_shader_review_game", "shader_review_games", ["game_version", "review_id"])
    op.create_table(
        "shader_review_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_id", sa.String(64), sa.ForeignKey("shader_reviews.id"), nullable=False),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("conclusion", sa.String(32), nullable=False),
        sa.Column("note", sa.String(4096), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("shader_review_decisions")
    op.drop_index("ix_shader_review_game", table_name="shader_review_games")
    op.drop_table("shader_review_games")
    op.drop_table("shader_reviews")
