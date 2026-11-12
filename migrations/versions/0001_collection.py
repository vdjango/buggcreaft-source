"""Initial durable collection queue and immutable artifact snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "artifacts",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "collection_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request", json_type, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(128)),
        sa.Column("artifact_sha256", sa.String(64), sa.ForeignKey("artifacts.sha256")),
    )
    op.create_index("ix_jobs_claim", "collection_jobs", ["state", "created_at"])
    op.create_table(
        "scans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "artifact_sha256", sa.String(64), sa.ForeignKey("artifacts.sha256"), nullable=False
        ),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("artifact_sha256", "parser_version", name="uq_scan_parser"),
    )


def downgrade() -> None:
    op.drop_table("scans")
    op.drop_index("ix_jobs_claim", table_name="collection_jobs")
    op.drop_table("collection_jobs")
    op.drop_table("artifacts")
