"""Durable cache-to-collector notifications; never parses metadata or resolves dependencies."""

import asyncio
import json
import logging
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientTimeout

from .public_download import public_download_response
from .dependency_discovery import DependencyDiscovery

TERMINAL = {"complete_declared", "partial", "unsupported", "not_applicable"}


class CacheFacts:
    def __init__(self, mirror):
        self.mirror = mirror
        self.origin = mirror.config.facts_url.rstrip("/")
        self.token = mirror.config.facts_token
        self.job = None
        self.closing = False
        self.discovery = DependencyDiscovery(self)
        if self.origin:
            parsed = urlsplit(self.origin)
            if (
                parsed.scheme not in ("https", "http")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.scheme == "http"
                and parsed.hostname not in ("127.0.0.1", "::1")
            ):
                raise ValueError("facts_endpoint_requires_https_or_loopback")
            if len(self.token) < 32:
                raise ValueError("facts_service_token_required")

    async def open(self):
        await self.mirror.db.executescript("""
            CREATE TABLE IF NOT EXISTS cache_facts(
                scope TEXT NOT NULL,digest TEXT NOT NULL,size INTEGER NOT NULL,
                kind TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
                accepted INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL NOT NULL DEFAULT 0,error TEXT,parser_version TEXT,
                PRIMARY KEY(scope,digest));
            CREATE INDEX IF NOT EXISTS cache_facts_due ON cache_facts(next_at);
            CREATE INDEX IF NOT EXISTS cache_facts_state ON cache_facts(state);
            CREATE TABLE IF NOT EXISTS cache_facts_cursor(
                scope TEXT PRIMARY KEY,after_key TEXT NOT NULL DEFAULT '',
                next_at REAL NOT NULL DEFAULT 0);
        """)
        await self.mirror.db.commit()
        await self.discovery.open()

    @staticmethod
    def read_header(path):
        with path.open("rb") as stream:
            return stream.read(4)

    async def remember(self, scope, digest, size, name, *, header=None, commit=True):
        """Same cache commit where possible; reconciliation covers cross-ledger/crash gaps."""
        path = urlsplit(name).path.lower()
        kind = (
            "jar"
            if path.endswith((".jar", ".jar.disabled"))
            else "archive"
            if path.endswith((".zip", ".mrpack", ".buggpack"))
            else "other"
        )
        if kind == "other":
            if header is None:
                local = (
                    self.mirror.root / "objects" / digest[:2] / digest
                    if scope == "mirror"
                    else self.mirror.room_cache.path(digest)
                )
                try:
                    header = await asyncio.to_thread(self.read_header, local)
                except OSError:
                    # Unknown/cloud-only content must not be declared dependency-free.
                    kind = "archive"
            if header in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
                kind = "archive"
        applicable = kind != "other"
        await self.mirror.db.execute(
            """INSERT INTO cache_facts(scope,digest,size,kind,state,accepted,next_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope,digest) DO UPDATE SET
            kind=excluded.kind,state='pending',accepted=0,next_at=0
            WHERE kind='other' AND excluded.kind!='other'""",
            (
                scope,
                digest,
                size,
                kind,
                "pending" if applicable else "not_applicable",
                0 if applicable else 1,
                0 if applicable else 1e20,
            ),
        )
        if commit:
            await self.mirror.db.commit()

    def schedule(self):
        if not self.closing and self.origin and (self.job is None or self.job.done()):
            if self.job and not self.job.cancelled() and self.job.exception():
                logging.warning("Cache facts notification failed; durable events will retry")
            self.job = asyncio.create_task(self.run_once())

    async def close(self):
        self.closing = True
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)

    async def reconcile(self, scope):
        position = await self.mirror.one("SELECT * FROM cache_facts_cursor WHERE scope=?", (scope,))
        if position and position["next_at"] > time.time():
            return
        after = position["after_key"] if position else ""
        if scope == "mirror":
            async with self.mirror.db.execute(
                "SELECT url,object,size FROM cache WHERE url>? ORDER BY url LIMIT 100", (after,)
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                await self.remember(scope, row["object"], row["size"], row["url"], commit=False)
            after = rows[-1]["url"] if rows else ""
        else:
            async with self.mirror.room_cache.db.execute(
                """SELECT o.digest,o.size,
                (SELECT MIN(path) FROM files f WHERE f.digest=o.digest) name,
                EXISTS(SELECT 1 FROM files f WHERE f.digest=o.digest AND path LIKE '%.jar') jar
                FROM objects o WHERE o.digest>? ORDER BY o.digest LIMIT 100""",
                (after,),
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                await self.remember(
                    scope,
                    row["digest"],
                    row["size"],
                    "artifact.jar" if row["jar"] else row["name"] or "",
                    commit=False,
                )
            after = rows[-1]["digest"] if rows else ""
        await self.mirror.db.execute(
            """INSERT INTO cache_facts_cursor VALUES(?,?,?)
            ON CONFLICT(scope) DO UPDATE SET
                after_key=excluded.after_key,next_at=excluded.next_at""",
            (scope, after, 0 if rows else time.time() + 3600),
        )
        await self.mirror.db.commit()

    async def service(self, method, path, *, limit=16384, **kwargs):
        """Trusted configured endpoint, original metadata pool, no redirects or public paths."""
        timeout = kwargs.pop("timeout", ClientTimeout(total=180, connect=10, sock_read=60))
        async with self.mirror.probe_http.request(
            method,
            self.origin + path,
            headers={"Authorization": "Bearer " + self.token},
            allow_redirects=False,
            timeout=timeout,
            **kwargs,
        ) as response:
            raw = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                raw.extend(chunk)
                if len(raw) > limit:
                    raise ValueError("facts_response_size_limit")
            if response.status != 200:
                raise ValueError("facts_service_http_" + str(response.status))
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("facts_response_invalid_object")
            return result

    async def request(self, method, digest, **kwargs):
        result = await self.service(method, "/api/v1/internal/cached-artifacts/" + digest, **kwargs)
        if result.get("sha256") != digest:
            raise ValueError("facts_response_identity_mismatch")
        return result

    async def local_chunks(self, path):
        with path.open("rb") as stream:
            while chunk := await asyncio.to_thread(stream.read, 128 * 1024):
                yield chunk

    async def process(self, row):
        digest, scope = row["digest"], row["scope"]
        try:
            state = await self.request("GET", digest)
            registration = (
                scope == "mirror"
                and not state.get("published")
                or row["kind"] != "other"
                and state.get("kind") == "other"
            )
            parser_changed = row["parser_version"] is not None and row[
                "parser_version"
            ] != state.get("parser_version")
            if state["state"] in ("missing", "pending") or registration or parser_changed:
                # Pending may already be in the original worker queue. Avoid
                # resending complete files while that worker owns the bytes.
                resend = (
                    registration
                    or state["state"] == "missing"
                    or not row["accepted"]
                    or parser_changed
                )
                if resend:
                    path = (
                        self.mirror.root / "objects" / digest[:2] / digest
                        if scope == "mirror"
                        else self.mirror.room_cache.path(digest)
                    )
                    params = dict(
                        kind=row["kind"],
                        size=row["size"],
                        visibility="public" if scope == "mirror" else "private",
                    )
                    if row["kind"] != "other":
                        if path.is_file():
                            state = await self.request(
                                "PUT", digest, params=params, data=self.local_chunks(path)
                            )
                        elif state.get("blob_available"):
                            # Collector CAS survives local cache cleanup and parser upgrades.
                            state = await self.request(
                                "PUT", digest, params=dict(params, reuse=True), data=b""
                            )
                        else:
                            location = await self.mirror.cloud.download_url(
                                scope, digest, reserve=True
                            )
                            if not location:
                                raise ValueError("facts_artifact_unavailable")
                            # Existing location owner reserves OSS egress before this read;
                            # the collector independently verifies the resulting bytes.
                            async with public_download_response(
                                self.mirror.http, location, timeout=180
                            ) as response:
                                response.raise_for_status()
                                state = await self.request(
                                    "PUT",
                                    digest,
                                    params=params,
                                    data=response.content.iter_chunked(128 * 1024),
                                )
                    else:
                        state = await self.request("PUT", digest, params=params, data=b"")
            if state["state"] not in TERMINAL | {"pending", "running", "failed"}:
                raise ValueError("facts_response_state_invalid")
            delay = 86400 if state["state"] in TERMINAL | {"failed"} else 60
            await self.mirror.db.execute(
                """UPDATE cache_facts SET state=?,accepted=1,
                attempts=0,next_at=?,error=?,parser_version=? WHERE scope=? AND digest=?""",
                (
                    state["state"],
                    time.time() + delay,
                    state.get("error"),
                    state.get("parser_version"),
                    scope,
                    digest,
                ),
            )
        except (ClientError, TimeoutError, OSError, ValueError, KeyError):
            # Error codes contain neither service credentials nor signed URLs.
            attempts = row["attempts"] + 1
            await self.mirror.db.execute(
                """UPDATE cache_facts SET state='pending',
                attempts=?,next_at=?,error='facts_notification_failed'
                WHERE scope=? AND digest=?""",
                (attempts, time.time() + min(3600, 30 * 2 ** min(attempts, 7)), scope, digest),
            )
        await self.mirror.db.commit()

    async def run_once(self):
        await self.discovery.run_once()
        await self.reconcile("mirror")
        await self.reconcile("room")
        async with self.mirror.db.execute(
            "SELECT * FROM cache_facts WHERE next_at<=? ORDER BY next_at LIMIT 8", (time.time(),)
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            await self.process(row)

    async def needs_local(self, scope, digest, *, allow_cloud=False):
        if not self.origin:
            return False
        row = await self.mirror.one(
            "SELECT kind,accepted,attempts FROM cache_facts WHERE scope=? AND digest=?",
            (scope, digest),
        )
        return (
            row is not None
            and row["kind"] != "other"
            and not row["accepted"]
            and (not allow_cloud or row["attempts"] < 3)
        )

    async def status(self):
        async with self.mirror.db.execute(
            "SELECT state,COUNT(*) count FROM cache_facts GROUP BY state"
        ) as cursor:
            states = {row["state"]: row["count"] for row in await cursor.fetchall()}
        return dict(
            configured=bool(self.origin),
            running=bool(self.job and not self.job.done()),
            states=states,
        )
