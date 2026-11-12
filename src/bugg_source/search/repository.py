"""PostgreSQL owns distributed feed/job leases, atomic heads and cursor sessions."""

import hashlib
import json
import secrets

from sqlalchemy import text

from .clients import SearchUnavailable
from .config import SearchSettings
from .text import config_revision


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class SearchRepository:
    def __init__(self, engine, settings: SearchSettings):
        self.engine, self.settings = engine, settings
        self.config = config_revision(settings.model_revision)

    def ready(self):
        if not self.settings.enabled or self.engine.dialect.name != "postgresql":
            raise SearchUnavailable("search_requires_enabled_postgresql")

    def feed_claim(self):
        self.ready()
        token = secrets.token_urlsafe(24)
        with self.engine.begin() as db:
            row = (
                db.execute(
                    text("""
                UPDATE search_control SET feed_token=:token,
                feed_until=now()+interval '60 seconds' WHERE id=1 AND
                (feed_until IS NULL OR feed_until<now()) RETURNING
                cursor,epoch
                """),
                    {"token": token},
                )
                .mappings()
                .first()
            )
        return (token, dict(row)) if row else None

    def feed_commit(self, token, batch):
        with self.engine.begin() as db:
            state = (
                db.execute(
                    text(
                        """
                SELECT * FROM search_control WHERE id=1 AND
                feed_token=:token AND feed_until>now() FOR UPDATE
                """
                    ),
                    {"token": token},
                )
                .mappings()
                .first()
            )
            if not state:
                return False
            if state["epoch"] and state["epoch"] != batch["epoch"]:
                raise SearchUnavailable("search_source_epoch_changed_reinitialize_required")
            for item in batch["items"]:
                current = db.execute(
                    text("""
                INSERT INTO
                search_projects(project_id,revision,visible,payload)
                VALUES(:id,:revision,:visible,CAST(:payload AS jsonb)) ON
                CONFLICT(project_id) DO UPDATE SET
                revision=excluded.revision,visible=excluded.visible,payload=excluded.payload
                WHERE search_projects.revision<excluded.revision RETURNING
                project_id
                """),
                    dict(
                        id=item["project_id"],
                        revision=item["revision"],
                        visible=item["visible"],
                        payload=encoded(item),
                    ),
                ).first()
                if current:
                    db.execute(
                        text("""
                INSERT INTO search_jobs(project_id,revision)
                VALUES(:id,:revision) ON CONFLICT(project_id) DO UPDATE SET
                revision=excluded.revision,state='queued',attempts=0,
                claim_token=NULL,lease_until=NULL,error=NULL,next_attempt=now(),updated_at=now()
                """),
                        dict(id=item["project_id"], revision=item["revision"]),
                    )
            db.execute(
                text("""
                UPDATE search_control SET
                epoch=:epoch,cursor=:cursor,watermark=:watermark,
                feed_until=NULL,feed_token=NULL,updated_at=now() WHERE id=1
                """),
                batch,
            )
        return True

    def feed_release(self, token):
        with self.engine.begin() as db:
            db.execute(
                text(
                    """
                UPDATE search_control SET feed_until=NULL,feed_token=NULL
                WHERE id=1 AND feed_token=:token
                """
                ),
                {"token": token},
            )

    def claim(self):
        token = secrets.token_urlsafe(24)
        with self.engine.begin() as db:
            db.execute(
                text(
                    """
                UPDATE search_jobs SET
                state='failed',error='lease_attempts_exhausted' WHERE
                state='running' AND lease_until<now() AND attempts>=5
                """
                )
            )
            row = db.execute(
                text("""
                SELECT j.project_id FROM search_jobs j WHERE
                ((j.state='queued' AND j.next_attempt<=now()) OR
                (j.state='running' AND j.lease_until<now())) AND
                j.attempts<5 ORDER BY j.updated_at,j.project_id FOR UPDATE
                SKIP LOCKED LIMIT 1
                """)
            ).first()
            if not row:
                return None
            job = (
                db.execute(
                    text("""
                UPDATE search_jobs SET state='running',claim_token=:token,
                lease_until=now()+interval '120
                seconds',attempts=attempts+1,node_id=:node,updated_at=now()
                WHERE project_id=:id RETURNING *
                """),
                    dict(id=row[0], token=token, node=self.settings.node_id),
                )
                .mappings()
                .one()
            )
            payload = db.execute(
                text("""
                SELECT payload FROM search_projects WHERE project_id=:id
                """),
                {"id": row[0]},
            ).scalar_one()
            return dict(job, payload=payload)

    def pulse(self, job):
        with self.engine.begin() as db:
            result = db.execute(
                text("""
                UPDATE search_jobs SET lease_until=now()+interval '120
                seconds' WHERE project_id=:project_id AND
                claim_token=:claim_token AND state='running' AND
                lease_until>now()
                """),
                job,
            )
            if result.rowcount != 1:
                raise SearchUnavailable("search_lease_lost")

    def publish(self, job, chunks, coverage, *, semantic_error=None):
        with self.engine.begin() as db:
            current = db.execute(
                text("""
                SELECT j.* FROM search_jobs j JOIN search_projects p
                USING(project_id) WHERE j.project_id=:project_id AND
                j.claim_token=:claim_token AND j.state='running' AND
                j.lease_until>now() AND p.revision=j.revision AND
                j.revision=:revision FOR UPDATE OF j,p
                """),
                job,
            ).first()
            if not current:
                return False
            db.execute(
                text("""
                DELETE FROM search_chunks WHERE project_id=:project_id
                """),
                job,
            )
            for chunk in chunks:
                db.execute(
                    text("""
                INSERT INTO
                search_chunks(project_id,revision,field,language,is_translation,
                source_hash,document_revision,ordinal,start_char,text,tokens,embedding,model_revision)
                VALUES(:project_id,:revision,:field,:language,:is_translation,:source_hash,:document_revision,
                :ordinal,:start_char,:text,to_tsvector('simple',:tokens),CAST(:embedding
                AS vector),:model_revision)
                """),
                    dict(
                        chunk,
                        project_id=job["project_id"],
                        revision=job["revision"],
                        embedding=encoded(chunk["embedding"]) if chunk.get("embedding") else None,
                        model_revision=self.settings.model_revision
                        if chunk.get("embedding")
                        else None,
                    ),
                )
            db.execute(
                text("""
                UPDATE search_projects SET
                indexed_revision=:revision,config_revision=:config,
                semantic_revision=:semantic,coverage=CAST(:coverage AS
                jsonb),indexed_at=now() WHERE project_id=:project_id
                """),
                dict(
                    job,
                    config=self.config,
                    semantic=self.settings.model_revision
                    if chunks and not semantic_error and self.settings.embedding_url
                    else None,
                    coverage=encoded(coverage),
                ),
            )
            db.execute(
                text("""
                UPDATE search_jobs SET state=CASE WHEN CAST(:error AS text)
                IS NULL THEN 'succeeded' WHEN attempts>=5 THEN 'failed' ELSE
                'queued' END,error=:error,claim_token=NULL,
                lease_until=NULL,next_attempt=now()+interval '30
                seconds',updated_at=now() WHERE project_id=:project_id
                """),
                dict(job, error=semantic_error),
            )
        return True

    def fail(self, job, error):
        with self.engine.begin() as db:
            db.execute(
                text("""
                UPDATE search_jobs SET state=CASE WHEN attempts>=5 THEN
                'failed' ELSE 'queued' END,
                error=:error,claim_token=NULL,lease_until=NULL,next_attempt=now()+interval
                '30 seconds',updated_at=now() WHERE project_id=:project_id
                AND claim_token=:claim_token AND state='running' AND
                lease_until>now()
                """),
                dict(job, error=error[:160]),
            )

    def rebuild(self, *, reset=False):
        self.ready()
        with self.engine.begin() as db:
            db.execute(
                text("""
                SELECT id FROM search_control WHERE id=1 FOR UPDATE
                """)
            )
            if reset:
                db.execute(
                    text("""
                DELETE FROM search_projects
                """)
                )
            else:
                db.execute(
                    text("""
                INSERT INTO search_jobs(project_id,revision) SELECT
                project_id,revision FROM search_projects ON
                CONFLICT(project_id) DO UPDATE SET
                state='queued',attempts=0,error=NULL,claim_token=NULL,
                lease_until=NULL,next_attempt=now(),updated_at=now()
                """)
                )
            db.execute(
                text("""
                DELETE FROM search_sessions
                """)
            )
            db.execute(
                text(
                    """
                UPDATE search_control SET cursor=0,epoch=CASE WHEN :reset
                THEN '' ELSE epoch END,feed_until=NULL,feed_token=NULL WHERE
                id=1
                """
                ),
                {"reset": reset},
            )
        return {"state": "queued", "reset": reset}

    def rate(self, identity, *, limit=120):
        bucket = hashlib.sha256(identity.encode()).hexdigest()
        with self.engine.begin() as db:
            count = db.execute(
                text("""
                INSERT INTO search_rate_limits
                VALUES(:bucket,floor(extract(epoch FROM
                now())/60)::bigint,1) ON CONFLICT(bucket) DO UPDATE SET
                rate_window=excluded.rate_window,count=CASE WHEN
                search_rate_limits.rate_window=excluded.rate_window THEN
                search_rate_limits.count+1 ELSE 1 END RETURNING count
                """),
                {"bucket": bucket},
            ).scalar_one()
        if count > limit:
            raise SearchUnavailable("search_rate_limited")

    def epoch(self):
        with self.engine.connect() as db:
            return db.execute(
                text("""
                SELECT epoch FROM search_control WHERE id=1
                """)
            ).scalar_one()

    def candidates(self, query, tokens, filters, vector=None):
        params = dict(
            config=self.config,
            query=query,
            tokens=" & ".join("'" + w.replace("'", "''") + "'" for w in tokens),
            model=self.settings.model_revision,
            vector=encoded(vector) if vector else None,
        )
        where = "p.visible AND p.indexed_revision=p.revision AND p.config_revision=:config"
        for key, path in (
            ("resource_type", "p.payload->>'resource_type'"),
            ("source", "p.payload->'resource'->>'provider'"),
        ):
            if filters.get(key):
                where += f" AND {path}=:{key}"
                params[key] = filters[key]
        if filters.get("mc_version") or filters.get("loader"):
            where += """
                AND EXISTS (SELECT 1 FROM
                jsonb_array_elements(COALESCE(p.payload->'metadata'->'releases','[]'))
                r WHERE true
                """
            for name, field in (("mc_version", "game_versions"), ("loader", "loaders")):
                if filters.get(name):
                    where += f" AND r->'{field}' @> CAST(:{name} AS jsonb)"
                    params[name] = encoded([filters[name]])
            where += ")"
        route_sql = {}
        if tokens:
            route_sql["lexical"] = (
                "c.tokens @@ to_tsquery('simple',:tokens)",
                "ts_rank_cd(c.tokens,to_tsquery('simple',:tokens))*"
                "CASE c.field WHEN 'title' THEN 4 WHEN 'summary' THEN 2 ELSE 1 END DESC",
            )
            route_sql["exact"] = (
                """c.field='title' AND (lower(c.text)=lower(:query)
                OR lower(p.project_id)=lower(:query)
                OR lower(p.payload->'metadata'->>'slug')=lower(:query))""",
                "c.id",
            )
            route_sql["fuzzy"] = (
                "c.field='title' AND c.text % :query",
                "similarity(c.text,:query) DESC",
            )
        if vector:
            route_sql["semantic"] = (
                "c.embedding IS NOT NULL AND c.model_revision=:model",
                "c.embedding <=> CAST(:vector AS vector)",
            )
        if not query:
            route_sql["browse"] = ("true", "p.project_id")
        result = {}
        with self.engine.begin() as db:
            db.execute(text("SET LOCAL statement_timeout='2500ms'"))
            for name, (condition, order) in route_sql.items():
                raw = (
                    db.execute(
                        text(f"""SELECT p.project_id,c.id AS chunk_id,c.revision
                    FROM search_chunks c
                    JOIN search_projects p ON p.project_id=c.project_id AND p.revision=c.revision
                    WHERE {where} AND ({condition}) ORDER BY {order},c.id LIMIT 1600"""),
                        params,
                    )
                    .mappings()
                    .all()
                )
                unique = {}
                for row in raw:
                    unique.setdefault(row["project_id"], dict(row))
                    if len(unique) == 200:
                        break
                result[name] = list(unique.values())
        return result

    def session(self, fingerprint, refs=None, metadata=None, cursor=None):
        with self.engine.begin() as db:
            if cursor:
                row = (
                    db.execute(
                        text("""
                SELECT * FROM search_sessions WHERE id=:id AND
                expires_at>now()
                """),
                        {"id": cursor},
                    )
                    .mappings()
                    .first()
                )
                if (
                    not row
                    or row["fingerprint"] != fingerprint
                    or row["config_revision"] != self.config
                ):
                    raise SearchUnavailable("search_session_expired_or_invalid")
                return dict(row)
            identifier = secrets.token_urlsafe(32)
            db.execute(
                text(
                    """
                DELETE FROM search_sessions WHERE id IN (SELECT id FROM
                search_sessions WHERE expires_at<now() LIMIT 200)
                """
                )
            )
            db.execute(
                text(
                    """
                DELETE FROM search_rate_limits WHERE
                rate_window<floor(extract(epoch FROM now())/60)-10
                """
                )
            )
            db.execute(
                text(
                    """
                INSERT INTO search_sessions
                VALUES(:id,:fingerprint,:config,CAST(:refs AS
                jsonb),CAST(:metadata AS jsonb),now()+interval '5 minutes')
                """
                ),
                dict(
                    id=identifier,
                    fingerprint=fingerprint,
                    config=self.config,
                    refs=encoded(refs),
                    metadata=encoded(metadata),
                ),
            )
            return dict(id=identifier, refs=refs, metadata=metadata)

    def materialize(self, refs):
        if not refs:
            return {}
        with self.engine.connect() as db:
            rows = (
                db.execute(
                    text("""
                SELECT c.id,c.project_id,c.revision,c.field,c.language,c.is_translation,
                c.source_hash,c.document_revision,c.text,p.payload,p.coverage FROM
                search_chunks c JOIN search_projects p USING(project_id)
                WHERE c.id=ANY(:ids) AND p.visible AND c.revision=p.revision
                AND p.indexed_revision=p.revision AND
                p.config_revision=:config
                """),
                    dict(ids=[r["chunk_id"] for r in refs], config=self.config),
                )
                .mappings()
                .all()
            )
        return {row["id"]: dict(row) for row in rows}

    def status(self):
        self.ready()
        with self.engine.connect() as db:
            state = dict(
                db.execute(
                    text("""
                SELECT epoch,cursor,watermark,updated_at FROM search_control
                WHERE id=1
                """)
                )
                .mappings()
                .one()
            )
            totals = dict(
                db.execute(
                    text("""
                SELECT count(*) AS projects,count(*) FILTER(WHERE visible
                AND indexed_revision=revision AND config_revision=:config)
                AS lexical_ready,count(*) FILTER(WHERE visible AND
                indexed_revision=revision AND config_revision=:config AND
                semantic_revision=:model) AS semantic_ready FROM
                search_projects
                """),
                    dict(config=self.config, model=self.settings.model_revision),
                )
                .mappings()
                .one()
            )
            jobs = [
                dict(row)
                for row in db.execute(
                    text(
                        """
                SELECT
                project_id,revision,state,attempts,node_id,error,updated_at
                FROM search_jobs ORDER BY updated_at DESC LIMIT 50
                """
                    )
                ).mappings()
            ]
        return dict(
            state,
            **totals,
            jobs=jobs,
            config_revision=self.config,
            model_revision=self.settings.model_revision or None,
        )
