"""Bounded publisher dependency discovery, sharing the mirror outbox and Collector.

API edges are discovery hints. Package facts stay in the independent facts service;
expanded does not mean that an environment has a compatible locked combination.
"""

import hashlib
import json
import time
from urllib.parse import quote

from aiohttp import ClientError, ClientTimeout

from .mirror_catalog import allowed_url


class DependencyDiscovery:
    def __init__(self, facts):
        self.facts, self.mirror = facts, facts.mirror

    async def open(self):
        await self.mirror.db.executescript("""
            CREATE TABLE IF NOT EXISTS dependency_releases(
                key TEXT PRIMARY KEY,provider TEXT NOT NULL,release_id TEXT NOT NULL,
                project_id TEXT NOT NULL,payload TEXT NOT NULL,origin TEXT NOT NULL,
                depth INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
                job_id TEXT,digest TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL NOT NULL DEFAULT 0,created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS dependency_releases_due ON dependency_releases(next_at);
            CREATE TABLE IF NOT EXISTS dependency_edges(
                parent TEXT NOT NULL,target TEXT NOT NULL,relation TEXT NOT NULL,
                child TEXT NOT NULL DEFAULT '',state TEXT NOT NULL,reason TEXT,
                PRIMARY KEY(parent,target,relation,child));
            CREATE TABLE IF NOT EXISTS dependency_bindings(
                provider TEXT NOT NULL,project_id TEXT NOT NULL,mod_id TEXT NOT NULL,
                loader TEXT NOT NULL,release_key TEXT NOT NULL,digest TEXT NOT NULL,
                PRIMARY KEY(provider,project_id,mod_id,loader,release_key));
        """)
        await self.mirror.db.commit()

    @staticmethod
    def release(provider, row, origin):
        """Official IDs/hashes only; no title/mod-id guessing or name version parsing."""
        if not isinstance(row, dict):
            return None
        if provider == "modrinth":
            files = row.get("files", [])
            file = next((f for f in files if f.get("primary")), files[0] if files else {})
            hashes = file.get("hashes", {})
            algorithm = next((k for k in ("sha512", "sha256", "sha1") if hashes.get(k)), None)
            if not algorithm:
                return None
            value = dict(
                provider=provider,
                release_id=row.get("id"),
                project_id=row.get("project_id"),
                url=file.get("url"),
                size=file.get("size"),
                digest=hashes[algorithm],
                algorithm=algorithm,
                game_versions=row.get("game_versions", []),
                loaders=row.get("loaders", []),
                dependencies=row.get("dependencies", []),
            )
        else:
            digest = next(
                (h.get("value") for h in row.get("hashes", []) if h.get("algo") == 1), None
            )
            value = dict(
                provider=provider,
                release_id=str(row.get("id", "")),
                project_id=str(row.get("modId", "")),
                url=row.get("downloadUrl"),
                size=row.get("fileLength"),
                digest=digest,
                algorithm="sha1",
                game_versions=row.get("gameVersions", []),
                loaders=[],
                dependencies=row.get("dependencies", []),
            )
        if (
            not value["release_id"]
            or not value["project_id"]
            or not value["digest"]
            or not isinstance(value["url"], str)
            or not allowed_url(value["url"])
            or not value["url"].split("?")[0].lower().endswith(".jar")
            or not isinstance(value["size"], int)
            or not 0 < value["size"] <= 256 * 1024**2
            or len(value["dependencies"]) > 128
        ):
            return None
        fields = (
            (
                "id",
                "project_id",
                "name",
                "version_type",
                "game_versions",
                "loaders",
                "files",
                "dependencies",
            )
            if provider == "modrinth"
            else (
                "id",
                "modId",
                "displayName",
                "releaseType",
                "gameVersions",
                "hashes",
                "fileName",
                "downloadUrl",
                "fileLength",
                "dependencies",
                "isServerPack",
                "isEarlyAccessContent",
                "isAvailable",
            )
        )
        return dict(value, origin=origin, publisher_data={k: row[k] for k in fields if k in row})

    async def observe(self, provider, releases, origin="request", depth=0):
        if provider not in ("modrinth", "curseforge") or origin not in (
            "request",
            "hotspot",
            "dependency",
        ):
            raise ValueError("invalid_dependency_origin")
        if not isinstance(releases, list) or len(releases) > 100:
            raise ValueError("dependency_observation_limit")
        count = await self.mirror.one("SELECT COUNT(*) count FROM dependency_releases")
        keys = []
        for row in releases:
            if isinstance(row, dict) and self.mirror.runtimes.release_blocked(row):
                continue
            value = self.release(provider, row, origin)
            if value is None:
                continue
            key = hashlib.sha256(
                json.dumps(
                    [provider, value["release_id"], value["algorithm"], value["digest"]]
                ).encode()
            ).hexdigest()
            existing = await self.mirror.one(
                "SELECT key FROM dependency_releases WHERE key=?", (key,)
            )
            if not existing and count["count"] + len(keys) >= 10000:
                continue
            await self.mirror.db.execute(
                """INSERT INTO dependency_releases
                (key,provider,release_id,project_id,payload,origin,depth,created_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                payload=excluded.payload, state='pending', next_at=0, error=NULL
                WHERE dependency_releases.payload != excluded.payload""",
                (
                    key,
                    provider,
                    value["release_id"],
                    value["project_id"],
                    json.dumps(value),
                    origin,
                    depth,
                    time.time(),
                ),
            )
            keys.append(key)
        await self.mirror.db.commit()
        return keys

    async def edge(self, parent, target, relation, child="", reason=None):
        await self.mirror.db.execute(
            """INSERT INTO dependency_edges VALUES(?,?,?,?,?,?)
            ON CONFLICT(parent,target,relation,child) DO UPDATE SET state=excluded.state,reason=excluded.reason""",
            (parent, target, relation, child, "discovered" if child else "unresolved", reason),
        )

    async def expand(self, row, facts):
        payload = json.loads(row["payload"])
        digest = facts["artifact_sha256"]
        for mod in facts.get("mods", []):
            # A nested bundled library does not establish the publisher identity
            # of that library's independently distributed project.
            if mod.get("container_path") != "root":
                continue
            await self.mirror.db.execute(
                "INSERT OR IGNORE INTO dependency_bindings VALUES(?,?,?,?,?,?)",
                (
                    row["provider"],
                    row["project_id"],
                    mod["mod_id"],
                    mod["loader"],
                    row["key"],
                    digest,
                ),
            )
        # Actual declarations define which absent mod IDs still need a trustworthy
        # project binding. Publisher references are followed without inventing one.
        declared = {
            d["target_id"]
            for m in facts.get("mods", [])
            for d in m.get("dependencies", [])
            if d.get("relation") in ("required", "depends")
        }
        builtin = {"minecraft", "neoforge", "forge", "fabricloader", "java", "quilt_loader"}
        for target in sorted(declared - builtin):
            async with self.mirror.db.execute(
                "SELECT DISTINCT project_id FROM dependency_bindings WHERE provider=? AND mod_id=? LIMIT 3",
                (row["provider"], target),
            ) as cursor:
                projects = [r["project_id"] for r in await cursor.fetchall()]
            if len(projects) != 1:
                await self.edge(
                    row["key"],
                    "mod:" + target,
                    "required",
                    reason="project_binding_ambiguous" if projects else "project_binding_required",
                )
                continue
            # Follow an evidenced project binding; a filename/title match is
            # never sufficient. Actual candidate facts still decide constraints.
            keys = await self.follow(row, payload, projects[0], None)
            for key in keys:
                await self.edge(row["key"], "mod:" + target, "required", key)
            await self.edge(
                row["key"],
                "mod:" + target,
                "required",
                reason="candidate_facts_pending" if keys else "no_published_candidate",
            )
        for dependency in payload["dependencies"]:
            if row["provider"] == "modrinth":
                if dependency.get("dependency_type") != "required":
                    continue
                project, release = dependency.get("project_id"), dependency.get("version_id")
            else:
                if dependency.get("relationType") != 3:
                    continue
                project, release = str(dependency.get("modId", "")), None
            target = str(release or project or "")
            if not target or len(target) > 128 or not all(c.isalnum() or c in "_-" for c in target):
                continue
            keys = await self.follow(row, payload, project, release)
            for key in keys:
                await self.edge(row["key"], target, "required", key)
            if not keys:
                await self.edge(
                    row["key"],
                    target,
                    "required",
                    reason="depth_budget" if row["depth"] >= 8 else "no_published_candidate",
                )
        # Revisit unresolved declarations when a later child yields its real
        # package identity. This also closes cycles without a recursive worker.
        await self.bind_edges()
        await self.mirror.db.commit()

    async def follow(self, row, payload, project, release):
        if row["depth"] >= 8:
            return []
        get = self.mirror.resources.get
        if row["provider"] == "modrinth":
            if release:
                candidates = [
                    await get("modrinth", "/version/" + quote(release, safe=""), observe=False)
                ]
            elif project:
                params = dict(
                    game_versions=json.dumps(payload["game_versions"][:32]),
                    loaders=json.dumps(payload["loaders"]),
                )
                candidates = (
                    await get(
                        "modrinth",
                        "/project/" + quote(project, safe="") + "/version",
                        params,
                        observe=False,
                    )
                )[:5]
            else:
                candidates = []
        else:
            params = dict(pageSize=5, index=0)
            if payload["game_versions"]:
                params["gameVersion"] = payload["game_versions"][0]
            candidates = (
                await get("curseforge", "/mods/" + project + "/files", params, observe=False)
            ).get("data", [])[:5]
        if project:
            candidates = [
                c
                for c in candidates
                if str(c.get("project_id") if row["provider"] == "modrinth" else c.get("modId"))
                == str(project)
            ]
        return await self.observe(row["provider"], candidates, "dependency", row["depth"] + 1)

    async def bind_edges(self):
        # Only bind a declaration to actually parsed children in the same
        # provider. Candidate compatibility is deliberately not asserted here.
        await self.mirror.db.execute("""DELETE FROM dependency_edges AS e
            WHERE e.child='' AND e.target LIKE 'mod:%' AND EXISTS(
              SELECT 1 FROM dependency_edges c JOIN dependency_bindings b ON b.release_key=c.child
              WHERE c.parent=e.parent AND b.mod_id=substr(e.target,5))""")
        await self.mirror.db.execute("""INSERT OR IGNORE INTO dependency_edges
            SELECT e.parent,'mod:'||b.mod_id,'required',e.child,'discovered',NULL
            FROM dependency_edges e JOIN dependency_bindings b ON b.release_key=e.child
            WHERE e.child!='' AND e.target NOT LIKE 'mod:%'""")
        await self.mirror.db.commit()

    async def run_once(self):
        if not self.facts.origin:
            return
        async with self.mirror.db.execute(
            "SELECT * FROM dependency_releases WHERE next_at<=? AND state!='expanded' ORDER BY next_at,created_at LIMIT 4",
            (time.time(),),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"])
                policy = self.mirror.runtimes.policy
                if (
                    row["release_id"] in policy.get("version_ids", [])
                    or payload["url"] in policy.get("urls", [])
                    or payload["algorithm"] == "sha1"
                    and payload["digest"] in policy.get("sha1s", [])
                ):
                    await self.mirror.db.execute(
                        "UPDATE dependency_releases SET state='blocked',error='distribution_blocked',next_at=? WHERE key=?",
                        (time.time() + 86400, row["key"]),
                    )
                    await self.mirror.db.commit()
                    continue
                result = await self.facts.service(
                    "POST",
                    "/api/v1/internal/dependency-releases",
                    json=payload,
                    limit=2 * 1024**2,
                    timeout=ClientTimeout(total=15),
                )
                if result.get("paused"):
                    break
                job = result["job"]
                facts = result.get("facts")
                state = job["state"]
                if facts:
                    await self.expand(row, facts)
                    state = "expanded" if facts["status"] == "complete_declared" else "partial"
                await self.mirror.db.execute(
                    """UPDATE dependency_releases SET state=?,job_id=?,digest=?,
                    error=?,next_at=? WHERE key=?""",
                    (
                        state,
                        job["id"],
                        job.get("artifact_sha256"),
                        job.get("error"),
                        time.time() + (86400 if state in ("failed", "partial") else 30),
                        row["key"],
                    ),
                )
            except (OSError, ValueError, KeyError, TimeoutError, ClientError):
                await self.mirror.db.execute(
                    """UPDATE dependency_releases SET state='pending',attempts=attempts+1,
                    error='dependency_discovery_retry',next_at=? WHERE key=?""",
                    (time.time() + min(3600, 30 * 2 ** min(row["attempts"], 7)), row["key"]),
                )
            await self.mirror.db.commit()

    async def status(self, retry=False):
        if retry:
            async with self.mirror.db.execute(
                "SELECT DISTINCT job_id FROM dependency_releases WHERE state IN ('failed','cancelled') AND job_id IS NOT NULL LIMIT 100"
            ) as cursor:
                jobs = [r["job_id"] for r in await cursor.fetchall()]
            for job in jobs:
                try:
                    await self.facts.service(
                        "POST",
                        "/api/v1/internal/dependency-jobs/" + quote(job, safe="") + "/retry",
                        json={},
                    )
                except (ClientError, TimeoutError, OSError, ValueError):
                    # Keep visible, retryable state; never pretend a failed
                    # original Collector job has restarted.
                    continue
            await self.mirror.db.execute(
                "UPDATE dependency_releases SET next_at=0,error=NULL WHERE state IN ('pending','failed','cancelled','partial')"
            )
            await self.mirror.db.execute(
                "UPDATE cache_facts SET next_at=0,error=NULL WHERE state NOT IN ('complete_declared','not_applicable')"
            )
            await self.mirror.db.commit()
        async with self.mirror.db.execute(
            "SELECT state,COUNT(*) count FROM dependency_releases GROUP BY state"
        ) as cursor:
            states = {r["state"]: r["count"] for r in await cursor.fetchall()}
        async with self.mirror.db.execute(
            "SELECT provider,release_id,origin,depth,state,error FROM dependency_releases ORDER BY created_at DESC LIMIT 50"
        ) as cursor:
            items = [dict(row) for row in await cursor.fetchall()]
        unresolved = await self.mirror.one(
            "SELECT COUNT(*) count FROM dependency_edges WHERE state='unresolved'"
        )
        return dict(
            configured=bool(self.facts.origin),
            states=states,
            items=items,
            unresolved_bindings=unresolved["count"],
            cache=await self.facts.status(),
        )
