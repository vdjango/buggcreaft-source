"""Shared search feed, token-fenced jobs, text/vector projections and cursors."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return  # Deliberately no SQLite imitation of PostgreSQL/vector search.
    op.execute("""
                CREATE EXTENSION IF NOT EXISTS vector
                """)
    op.execute("""
                CREATE EXTENSION IF NOT EXISTS pg_trgm
                """)
    statements = [
        """
                CREATE TABLE search_control(id integer PRIMARY KEY
                CHECK(id=1),epoch text NOT NULL DEFAULT '', cursor bigint
                NOT NULL DEFAULT 0,watermark bigint NOT NULL DEFAULT 0,
                feed_token text,feed_until timestamptz,updated_at
                timestamptz NOT NULL DEFAULT now())
                """,
        """
                INSERT INTO search_control(id) VALUES(1)
                """,
        """
                CREATE TABLE search_projects(project_id text PRIMARY
                KEY,revision bigint NOT NULL, visible boolean NOT
                NULL,payload jsonb NOT NULL,indexed_revision bigint,
                config_revision text,semantic_revision text,coverage jsonb
                NOT NULL DEFAULT '{}', indexed_at timestamptz)
                """,
        """
                CREATE TABLE search_jobs(project_id text PRIMARY KEY
                REFERENCES search_projects ON DELETE CASCADE, revision
                bigint NOT NULL,state text NOT NULL DEFAULT
                'queued',attempts integer NOT NULL DEFAULT 0, claim_token
                text,lease_until timestamptz,next_attempt timestamptz NOT
                NULL DEFAULT now(), node_id text,error text,updated_at
                timestamptz NOT NULL DEFAULT now())
                """,
        """
                CREATE INDEX search_job_claim ON
                search_jobs(state,next_attempt,lease_until)
                """,
        """
                CREATE TABLE search_chunks(id bigserial PRIMARY
                KEY,project_id text NOT NULL REFERENCES search_projects ON
                DELETE CASCADE, revision bigint NOT NULL,field text NOT
                NULL,language text NOT NULL,is_translation boolean NOT NULL,
                source_hash text NOT NULL,document_revision text NOT
                NULL,ordinal integer NOT NULL,start_char integer NOT NULL,
                text text NOT NULL,tokens tsvector NOT NULL,embedding
                vector,model_revision text,
                UNIQUE(project_id,field,language,is_translation,ordinal))
                """,
        """
                CREATE INDEX search_chunks_project ON
                search_chunks(project_id,revision)
                """,
        """
                CREATE INDEX search_chunks_text ON search_chunks USING
                gin(tokens)
                """,
        """
                CREATE INDEX search_name_trgm ON search_chunks USING
                gin(text gin_trgm_ops) WHERE field='title'
                """,
        """
                CREATE TABLE search_sessions(id text PRIMARY KEY,fingerprint
                text NOT NULL, config_revision text NOT NULL,refs jsonb NOT
                NULL,metadata jsonb NOT NULL,expires_at timestamptz NOT
                NULL)
                """,
        """
                CREATE INDEX search_sessions_expiry ON
                search_sessions(expires_at)
                """,
        """
                CREATE TABLE search_rate_limits(bucket text PRIMARY
                KEY,rate_window bigint NOT NULL,count integer NOT NULL)
                """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        for name in (
            "search_rate_limits",
            "search_sessions",
            "search_chunks",
            "search_jobs",
            "search_projects",
            "search_control",
        ):
            op.execute("DROP TABLE " + name)
        # Extensions can have other owners; never drop them here.
