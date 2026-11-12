"""Transactional search feed owned by the catalogue writer, never by a worker."""

import hmac
import json
import os
import uuid

from aiohttp import web

from .hashing import digest
from .store import POLICY


class SearchExport:
    def __init__(self, store):
        self.store = store

    async def open(self):
        # Triggers cover all callers, including translation withdrawal and SQL deletion.
        await self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS search_feed_meta(
                id INTEGER PRIMARY KEY CHECK(id=1),epoch TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS search_changes(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS search_visibility(
                key TEXT PRIMARY KEY,revision INTEGER NOT NULL,visible INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS search_changes_key ON search_changes(key,seq);
        """)
        await self.store.db.execute(
            "INSERT OR IGNORE INTO search_feed_meta VALUES(1,?)", (str(uuid.uuid4()),)
        )
        for table, operation, condition, key in [
            ("resources", "INSERT", "", "new.key"),
            (
                "resources",
                "UPDATE",
                "WHEN old.snapshot_hash<>new.snapshot_hash OR old.snapshot<>new.snapshot",
                "new.key",
            ),
            ("resources", "DELETE", "", "old.key"),
            ("translations", "INSERT", "", "new.key"),
            ("translations", "UPDATE", "", "new.key"),
            ("translations", "DELETE", "", "old.key"),
        ]:
            visible = "0" if table == "resources" and operation == "DELETE" else "1"
            await self.store.db.execute(f"""
                CREATE TRIGGER IF NOT EXISTS search_{table}_{operation.lower()}
                AFTER {operation} ON {table} {condition} BEGIN
                INSERT INTO search_changes(key) VALUES({key});
                INSERT INTO search_visibility VALUES({key},last_insert_rowid(),{visible})
                ON CONFLICT(key) DO UPDATE SET revision=excluded.revision
                {",visible=0" if visible == "0" else ""}; END;
            """)
        async with self.store.transaction(invalidate=False) as db:
            await db.execute(
                "INSERT INTO search_changes(key) SELECT c.key FROM catalogue c "
                "LEFT JOIN search_visibility v ON c.key=v.key WHERE v.key IS NULL ORDER BY c.key"
            )
            await db.execute(
                "INSERT OR IGNORE INTO search_visibility SELECT key,MAX(seq),1 "
                "FROM search_changes GROUP BY key"
            )
        return self

    def authorize(self, request):
        expected = os.environ.get("BUGG_PLATFORM_AUTH_TOKEN", "")
        remote_allowed = os.environ.get("BUGG_SEARCH_ALLOW_REMOTE_EXPORT") == "1" and request.secure
        if (
            not expected
            or not hmac.compare_digest(
                expected.encode(), request.headers.get("X-Platform-Auth", "").encode()
            )
            or request.remote not in ("127.0.0.1", "::1")
            and not remote_allowed
        ):
            raise web.HTTPUnauthorized()

    async def record(self, db, key):
        visibility = await (
            await db.execute("SELECT * FROM search_visibility WHERE key=?", (key,))
        ).fetchone()
        row = await (
            await db.execute("SELECT snapshot FROM resources WHERE key=?", (key,))
        ).fetchone()
        if not row or not visibility or not visibility["visible"]:
            return dict(
                project_id=key, revision=visibility["revision"] if visibility else 0, visible=False
            )
        source = json.loads(row["snapshot"])
        documents = [
            dict(
                field=name,
                language=field.get("source_language", "und"),
                text=field["text"],
                format=field["format"],
                source_hash=field["source_hash"],
                is_translation=False,
                revision=field["source_hash"],
                quality="original",
            )
            for name, field in source["fields"].items()
            if name in ("title", "summary", "body")
        ]
        translations = await (
            await db.execute(
                "SELECT * FROM translations WHERE key=? AND policy=? AND context=?",
                (key, POLICY, self.store.context(source)),
            )
        ).fetchall()
        for tr in translations:
            if (
                tr["field"] in ("title", "summary", "body")
                and tr["quality"] in ("machine", "reviewed", "human")
                and source["fields"].get(tr["field"], {}).get("source_hash") == tr["source_hash"]
            ):
                documents.append(
                    dict(
                        field=tr["field"],
                        language=tr["language"],
                        text=tr["text"],
                        format=tr["format"],
                        source_hash=tr["source_hash"],
                        is_translation=True,
                        revision=tr["revision"],
                        quality=tr["quality"],
                    )
                )
        payload = dict(
            project_id=key,
            revision=visibility["revision"],
            visible=True,
            resource=source["resource"],
            resource_type=source["kind"],
            source_url=source["source_url"],
            metadata=source.get("metadata", {}),
            documents=documents,
        )
        payload["document_revision"] = digest(documents)
        return payload

    async def changes(self, request):
        self.authorize(request)
        after = int(request.query.get("after", "0"))
        limit = int(request.query.get("limit", "8"))
        if after < 0 or not 1 <= limit <= 16:
            raise web.HTTPBadRequest()
        async with self.store.transaction(invalidate=False) as db:
            epoch = (
                await (await db.execute("SELECT epoch FROM search_feed_meta WHERE id=1")).fetchone()
            )[0]
            events = await (
                await db.execute(
                    "SELECT seq,key FROM search_changes WHERE seq>? ORDER BY seq LIMIT ?",
                    (after, limit),
                )
            ).fetchall()
            records = [
                await self.record(db, key) for key in dict.fromkeys(row["key"] for row in events)
            ]
            watermark = (
                await (
                    await db.execute("SELECT COALESCE(MAX(seq),0) FROM search_changes")
                ).fetchone()
            )[0]
        return web.json_response(
            dict(
                epoch=epoch,
                cursor=events[-1]["seq"] if events else after,
                watermark=watermark,
                items=records,
            )
        )

    async def validate(self, request):
        self.authorize(request)
        data = await request.json()
        keys = data.get("projects")
        if (
            not isinstance(keys, list)
            or len(keys) > 200
            or any(not isinstance(k, str) or len(k) > 300 for k in keys)
        ):
            raise web.HTTPBadRequest()
        values = {}
        if keys:
            sql = (
                "SELECT v.* FROM search_visibility v JOIN resources r "
                "ON r.key=v.key WHERE v.key IN (" + ",".join("?" for _ in keys) + ")"
            )
            values = {
                row["key"]: dict(revision=row["revision"], visible=bool(row["visible"]))
                for row in await self.store.rows(sql, keys)
            }
        epoch = await self.store.one("SELECT epoch FROM search_feed_meta WHERE id=1")
        return web.json_response(dict(projects=values, epoch=epoch["epoch"]))

    async def visibility(self, request):
        self.authorize(request)
        data = await request.json()
        key, visible = data.get("project_id"), data.get("visible")
        if not isinstance(key, str) or len(key) > 300 or type(visible) is not bool:
            raise web.HTTPBadRequest()
        async with self.store.transaction() as db:
            if not await (
                await db.execute("SELECT key FROM resources WHERE key=?", (key,))
            ).fetchone():
                raise web.HTTPNotFound()
            cursor = await db.execute("INSERT INTO search_changes(key) VALUES(?)", (key,))
            await db.execute(
                "UPDATE search_visibility SET revision=?,visible=? WHERE key=?",
                (cursor.lastrowid, int(visible), key),
            )
        return web.json_response(dict(project_id=key, visible=visible))
