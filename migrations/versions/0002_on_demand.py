"""Content-aware admission, cache keys and bounded network work."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collection_jobs",
        sa.Column("parser_version", sa.String(32), nullable=False, server_default="1.0.0"),
    )
    op.add_column(
        "collection_jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default="100")
    )
    op.add_column(
        "collection_jobs",
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("collection_jobs", sa.Column("download_reservation_token", sa.String(36)))
    op.drop_index("ix_jobs_claim", table_name="collection_jobs")
    op.create_index("ix_jobs_claim", "collection_jobs", ["state", "priority", "created_at"])
    op.create_index("ix_jobs_lease", "collection_jobs", ["state", "lease_until"])
    op.create_table(
        "collection_keys",
        sa.Column("fingerprint", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("collection_jobs.id"), nullable=False),
    )
    op.create_table(
        "collection_gate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "download_budgets",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "artifact_hashes",
        sa.Column("algorithm", sa.String(16), primary_key=True),
        sa.Column("digest", sa.String(128), primary_key=True),
        sa.Column(
            "artifact_sha256", sa.String(64), sa.ForeignKey("artifacts.sha256"), nullable=False
        ),
    )


def downgrade() -> None:
    for name in ("artifact_hashes", "download_budgets", "collection_gate", "collection_keys"):
        op.drop_table(name)
    op.drop_index("ix_jobs_lease", table_name="collection_jobs")
    op.drop_index("ix_jobs_claim", table_name="collection_jobs")
    op.create_index("ix_jobs_claim", "collection_jobs", ["state", "created_at"])
    for name in ("download_reservation_token", "cache_hit", "priority", "parser_version"):
        op.drop_column("collection_jobs", name)
