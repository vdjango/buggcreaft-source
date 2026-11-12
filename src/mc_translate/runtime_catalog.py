"""One mirror-owned release catalogue and reversible distribution quarantine."""

import asyncio
import json
import re
import time
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from aiohttp import web

from .mirror_catalog import Inventory, allowed_url, route_for, write_json

CORES = {
    "iris": ("Iris Shaders", "YL57xq9U"),
    "sodium": ("Sodium", "AANobbMI"),
    "oculus": ("Oculus", "GchcoXML"),
    "embeddium": ("Embeddium", "sk9rgfiA"),
}
LOADERS = {
    "fabric": ("Fabric", "https://maven.fabricmc.net/net/fabricmc/fabric-loader/"),
    "quilt": ("Quilt", "https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-loader/"),
    "forge": ("Forge", "https://maven.minecraftforge.net/net/minecraftforge/forge/"),
    "neoforge": ("NeoForge", "https://maven.neoforged.net/releases/net/neoforged/neoforge/"),
}
FAMILIES = {**{k: v[0] for k, v in LOADERS.items()}, **{k: v[0] for k, v in CORES.items()}}
DEFAULTS = dict(enabled=False, cache_loaders=True, cores=list(CORES), refresh_hours=24)


def loader_bases(family):
    base = LOADERS[family][1]
    return [base, base.replace('/neoforge/', '/forge/')] if family == 'neoforge' else [base]


def validate_prefetch(value):
    result = {**DEFAULTS, **value} if isinstance(value, dict) else {}
    if (
        set(result) != set(DEFAULTS)
        or type(result.get("enabled")) is not bool
        or type(result.get("cache_loaders")) is not bool
        or type(result.get("refresh_hours")) is not int
        or not 1 <= result["refresh_hours"] <= 720
        or not isinstance(result.get("cores"), list)
        or any(k not in CORES for k in result["cores"])
    ):
        raise ValueError("缓存周期为 1–720 小时，请选择有效核心及加载器开关")
    return dict(result, cores=list(dict.fromkeys(result["cores"])))


def loader_identity(url):
    path = unquote(urlsplit(url).path)
    for family in LOADERS:
        # Legacy NeoForge 1.20.1 uses the forge artifact in the same publisher namespace.
        for root in (urlsplit(base).path for base in loader_bases(family)):
            if path.startswith(root):
                version = path[len(root) :].split("/")[0]
                if version and version != "maven-metadata.xml":
                    return family, version
    match = re.search(r"/v[23]/versions/loader/[^/]+/([^/]+)", path)
    host = urlsplit(url).hostname
    return (
        ("fabric" if host == "meta.fabricmc.net" else "quilt", match[1])
        if match and host in ("meta.fabricmc.net", "meta.quiltmc.org")
        else None
    )


def blocked_release(value, policy):
    """Apply explicit publisher identity/hash rules, never guessed compatibility."""
    if not isinstance(value, dict):
        return False
    rules = {(r["family"], r["version"]) for r in policy.get("blocks", [])}
    family = next(
        (key for key, (_, project) in CORES.items() if project == value.get("project_id")), None
    )
    return (
        (family, value.get("version_number")) in rules
        or value.get("id") in policy.get("version_ids", [])
        or any(
            f.get("hashes", {}).get("sha1") in policy.get("sha1s", [])
            for f in value.get("files", [])
        )
        or any(h.get('algo') == 1 and h.get('value') in policy.get('sha1s', []) for h in value.get('hashes', []) if isinstance(h, dict))
        or any(
            d.get("dependency_type") == "required"
            and d.get("version_id") in policy.get("version_ids", [])
            for d in value.get("dependencies", [])
        )
    )


class RuntimeCatalog:
    def __init__(self, mirror):
        self.mirror = mirror
        self.path = mirror.root / "runtime-distribution.json"
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.state = dict(blocks=[], next_at=0, requested=False)
        self.lock = asyncio.Lock()
        self.cleanup_lock = asyncio.Lock()
        self.metadata_lock = asyncio.Lock()
        self.policy = dict(blocks=self.state.get("blocks", []), version_ids=[], sha1s=[], urls=[])

    async def open(self):
        await self.mirror.db.executescript("""CREATE TABLE IF NOT EXISTS runtime_files(
            url TEXT PRIMARY KEY, family TEXT NOT NULL, version TEXT NOT NULL,
            release_id TEXT NOT NULL, game TEXT NOT NULL, loaders TEXT NOT NULL,
            size INTEGER, sha1 TEXT, updated REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS runtime_identity ON runtime_files(family,version);
            CREATE INDEX IF NOT EXISTS runtime_sha1 ON runtime_files(sha1);
            CREATE TABLE IF NOT EXISTS runtime_objects(digest TEXT PRIMARY KEY,sha1 TEXT NOT NULL,purged REAL NOT NULL DEFAULT 0);""")
        async with self.mirror.db.execute('PRAGMA table_info(runtime_objects)') as cursor:
            if 'purged' not in {r['name'] for r in await cursor.fetchall()}:
                await self.mirror.db.execute('ALTER TABLE runtime_objects ADD COLUMN purged REAL NOT NULL DEFAULT 0')
        await self.seed_loaders()
        await self.rebuild_policy()

    async def seed_loaders(self):
        await self.mirror.db.commit()
        prefixes = [base + '%' for family in LOADERS for base in loader_bases(family)]
        where = " OR ".join("url LIKE ?" for _ in prefixes)
        async with self.mirror.db.execute(
            "SELECT url,size,sha1 FROM expected WHERE "
            + where
            + " UNION SELECT url,size,sha1 FROM cache WHERE "
            + where,
            (*prefixes, *prefixes),
        ) as cursor:
            existing = await cursor.fetchall()
        for row in existing:
            identity = loader_identity(row["url"])
            if identity:
                await self.mirror.db.execute(
                    "INSERT INTO runtime_files VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET sha1=COALESCE(NULLIF(excluded.sha1,''),runtime_files.sha1),size=COALESCE(excluded.size,runtime_files.size)",
                    (row["url"], *identity, "", "", identity[0], row["size"], row["sha1"], 0),
                )
        await self.mirror.db.commit()

    async def rebuild_policy(self):
        async with self.mirror.db.execute(
            """SELECT r.* FROM json_each(?) b JOIN runtime_files r
            ON r.family=json_extract(b.value,'$.family') AND r.version=json_extract(b.value,'$.version')""",
            (json.dumps(self.state.get("blocks", [])),),
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
        blocked = rows
        self.policy = dict(
            blocks=self.state.get("blocks", []),
            version_ids=sorted({r["release_id"] for r in blocked if r["release_id"]}),
            sha1s=sorted({r["sha1"] for r in blocked if r["sha1"]}),
            urls=sorted({r["url"] for r in blocked}),
        )

    async def mark(self, data):
        family, version, blocked, reason = (
            data.get(k) for k in ("family", "version", "blocked", "reason")
        )
        if (
            family not in FAMILIES
            or not isinstance(version, str)
            or not re.fullmatch(r"[A-Za-z0-9_.+ -]{1,128}", version)
            or type(blocked) is not bool
            or not isinstance(reason, str)
            or len(reason) > 300
            or blocked
            and not reason.strip()
        ):
            raise ValueError("请选择类型、精确版本，并填写停供原因（最多 300 字）")
        async with self.lock:
            blocks = [
                r
                for r in self.state.get("blocks", [])
                if (r["family"], r["version"]) != (family, version)
            ]
            if blocked:
                blocks.append(
                    dict(family=family, version=version, reason=reason.strip(), at=time.time())
                )
            if len(blocks) > 1000:
                raise ValueError("停供规则最多 1000 条")
            state = dict(self.state, blocks=blocks, requested=True, next_at=0, cleanup_pending=bool(blocks))
            await asyncio.to_thread(write_json, self.path, state)
            self.state = state
            await self.seed_loaders()
            await self.rebuild_policy()
        return dict(ok=True)

    async def assert_allowed(self, url=None, digest=None):
        if not self.policy["blocks"]:
            return
        identity = loader_identity(url or "")
        rules = {(r["family"], r["version"]) for r in self.policy["blocks"]}
        rejected = self.is_blocked(url or "") or identity in rules
        if not rejected and self.policy["sha1s"]:
            item = await self.mirror.one(
                "SELECT sha1 FROM cache WHERE " + ("object=?" if digest else "url=?") + " LIMIT 1",
                (digest or url,),
            )
            if not item and url:
                item = await self.mirror.one("SELECT sha1 FROM expected WHERE url=?", (url,))
            if not item and digest:
                item = await self.mirror.one("SELECT sha1 FROM runtime_objects WHERE digest=?", (digest,))
            rejected = bool(item and item["sha1"] in self.policy["sha1s"])
        if rejected:
            raise web.HTTPForbidden(
                text="此加载器或核心版本已被源站标记为问题版本，暂停分发。",
                headers={"X-Bugg-Error": "runtime_blocked", "Cache-Control": "no-store"},
            )

    def release_blocked(self, release):
        return blocked_release(release, self.policy)

    async def cleanup(self):
        """Only quarantined bytes; preserve release identity so aliases stay blocked after deletion."""
        if not self.state.get('cleanup_pending'):
            return
        async with self.cleanup_lock:
            async with self.lock:
                await self.seed_loaders()
                await self.rebuild_policy()
                snapshot = list(self.policy['blocks'])
                async with self.mirror.db.execute('''SELECT DISTINCT c.object digest,c.sha1 FROM cache c
                WHERE c.url IN (SELECT value FROM json_each(?)) OR c.sha1 IN (SELECT value FROM json_each(?))''',
                    (json.dumps(self.policy['urls']), json.dumps(self.policy['sha1s']))) as cursor:
                    rows = await cursor.fetchall()
                await self.mirror.db.executemany('INSERT INTO runtime_objects VALUES(?,?,0) ON CONFLICT(digest) DO UPDATE SET sha1=excluded.sha1,purged=0',[(r['digest'],r['sha1'] or '') for r in rows])
                await self.mirror.db.commit()
                async with self.mirror.db.execute('SELECT digest FROM runtime_objects WHERE purged=0 AND sha1 IN (SELECT value FROM json_each(?))', (json.dumps(self.policy['sha1s']),)) as cursor:
                    digests = {r['digest'] for r in await cursor.fetchall()} | {r['digest'] for r in rows}
            pending, removed = len(digests)>5, 0
            for digest in sorted(digests)[:5]:
                if await self.purge(digest):removed += 1
                else:pending = True
            async with self.lock:
                self.state.update(cleanup_pending=bool(self.policy['blocks']) and (pending or self.policy['blocks']!=snapshot),cleanup_removed=removed,cleanup_at=time.time())
                await asyncio.to_thread(write_json,self.path,self.state)

    async def purge(self, digest):
        """One quarantined object, with the existing transfer/reservation locks."""
        async with self.mirror.space_lock, self.mirror.room_cache.lock, self.mirror.cloud.upload_lock:
            try:
                await self.assert_allowed(digest=digest)
            except web.HTTPForbidden:
                pass
            else:
                return True  # Restored while cleanup was queued: leave its bytes alone.
            if (self.mirror.serving.get(digest) or self.mirror.cloud.uploading(digest)
                    or self.mirror.room_cache.serving.get(digest) or digest in self.mirror.room_cache.uploading):
                return False
            for scope in ('mirror','room'):
                await self.mirror.cloud.delete_objects(scope,digest)
                await self.mirror.cloud.discard_jobs(scope,digest)
            obj = await self.mirror.one('SELECT size,local FROM objects WHERE digest=?',(digest,))
            await asyncio.to_thread((self.mirror.root/'objects'/digest[:2]/digest).unlink,missing_ok=True)
            count = (await self.mirror.one('SELECT COUNT(*) n FROM cache WHERE object=?',(digest,)))['n']
            await self.mirror.db.execute('DELETE FROM cache WHERE object=?',(digest,))
            await self.mirror.db.execute('DELETE FROM objects WHERE digest=?',(digest,))
            await self.mirror.db.execute('UPDATE totals SET bytes=MAX(0,bytes-?),files=MAX(0,files-?) WHERE id=1',(obj['size'] if obj and obj['local'] else 0,count))
            await self.mirror.db.execute('DELETE FROM resource_prefetch_files WHERE sha1 IN (SELECT sha1 FROM runtime_objects WHERE digest=?)',(digest,))
            await self.mirror.db.commit()
            await asyncio.to_thread(self.mirror.room_cache.path(digest).unlink,missing_ok=True)
            await self.mirror.room_cache.db.execute('UPDATE objects SET local=0 WHERE digest=?',(digest,))
            await self.mirror.room_cache.db.commit()
            await self.mirror.db.execute('UPDATE runtime_objects SET purged=? WHERE digest=?',(time.time(),digest))
            await self.mirror.db.commit()
            return True

    async def observe(self, releases):
        rows = []
        for release in releases[:2000]:
            if not isinstance(release, dict):
                continue
            family = next(
                (k for k, (_, project) in CORES.items() if release.get("project_id") == project),
                None,
            )
            if not family:
                continue
            for file in release.get("files", [])[:20]:
                url = file.get("url", "").replace("%2B", "+").replace("%2b", "+")
                if not allowed_url(url):
                    continue
                rows.append(
                    (
                        url,
                        family,
                        str(release.get("version_number", ""))[:128],
                        str(release.get("id", ""))[:128],
                        ", ".join(release.get("game_versions", []))[:500],
                        ", ".join(release.get("loaders", []))[:200],
                        file.get("size"),
                        file.get("hashes", {}).get("sha1", ""),
                        time.time(),
                    )
                )
        if rows:
            async with self.lock:
                await self.mirror.db.executemany(
                    "INSERT OR REPLACE INTO runtime_files VALUES(?,?,?,?,?,?,?,?,?)", rows
                )
                await self.mirror.db.commit()
                await self.rebuild_policy()

    async def files(self, data):
        offset, family, query = data.get("offset", 0), data.get("family", ""), data.get("query", "")
        if (
            type(offset) is not int
            or not 0 <= offset <= 100000
            or family
            and family not in FAMILIES
            or not isinstance(query, str)
            or len(query) > 100
        ):
            raise ValueError("版本列表查询无效")
        args = (family, family, "%" + query + "%", "%" + query + "%", "%" + query + "%")
        where = (
            "WHERE (?='' OR r.family=?) AND (r.version LIKE ? OR r.game LIKE ? OR r.family LIKE ?)"
        )
        total = await self.mirror.one("SELECT COUNT(*) total FROM runtime_files r " + where, args)
        async with self.mirror.db.execute(
            """SELECT r.*,c.object,
            (o.local=1 OR EXISTS(SELECT 1 FROM object_locations l WHERE l.scope='mirror' AND l.digest=c.object AND l.state='ready')) ready
            FROM runtime_files r LEFT JOIN cache c ON c.url=r.url LEFT JOIN objects o ON o.digest=c.object """
            + where
            + " ORDER BY r.updated DESC,r.family,r.version LIMIT 50 OFFSET ?",
            (*args, offset),
        ) as cursor:
            files = [dict(row) for row in await cursor.fetchall()]
        rules = {(r["family"], r["version"]): r["reason"] for r in self.policy["blocks"]}
        for row in files:
            row["reason"] = rules.get((row["family"], row["version"]), "")
            row["blocked"] = bool(row["reason"])
            row["path"] = route_for(row["url"])
        return dict(
            files=files,
            total=total["total"],
            offset=offset,
            has_more=offset + len(files) < total["total"],
            families=FAMILIES,
            blocks=self.policy["blocks"],
            state=self.state,
        )

    async def request(self):
        async with self.lock:
            self.state = dict(self.state, requested=True, next_at=0)
            await asyncio.to_thread(write_json, self.path, self.state)

    async def finished(self, job, settings):
        async with self.lock:
            self.state = dict(
                self.state,
                requested=self.state.get('cleanup_pending', False),
                last_state=job.get("state"),
                checked_at=time.time(),
                next_at=time.time()
                + (300 if job.get("state") != "finished" else settings["refresh_hours"] * 3600),
            )
            await asyncio.to_thread(write_json, self.path, self.state)

    async def discover(self, settings):
        """One existing mirror worker; metadata budget and transfers are reused."""
        await self.cleanup()
        from .mirror_resources import compatible_releases
        selected, inventory = [], Inventory(self.mirror.root)
        self.mirror.job_state.update(phase="runtime_catalogue", discovered=0)
        rules = settings['resource_prefetch']['compatibility']
        games = await self.mirror.resources.game_plan(rules)
        self.mirror.job_state['game_versions'] = games
        bases = [(family,base) for family in LOADERS for base in loader_bases(family)
                 if not (family=='neoforge' and base.endswith('/forge/') and '1.20.1' not in games)]
        for family, base in bases:
            if not settings['runtime_prefetch']['cache_loaders']:
                break
            xml = await inventory.fetch_json(
                self.mirror.http,
                base + "maven-metadata.xml",
                category="loader-metadata",
                decode=ET.fromstring,
            )
            versions = [
                item.text for item in xml.findall("./versioning/versions/version") if item.text
            ]
            releases = []
            if family in ('fabric', 'quilt'):
                endpoint = 'https://meta.fabricmc.net/v2' if family == 'fabric' else 'https://meta.quiltmc.org/v3'
                supported = {}
                game_metadata = await inventory.fetch_json(self.mirror.http,endpoint+'/versions/game',category='loader-metadata')
                supported_games = {row['version'] for row in game_metadata}
                for game in games:
                    if game not in supported_games:
                        continue
                    # Publisher API, not an assumption that every loader works with every game.
                    rows = await inventory.fetch_json(self.mirror.http, endpoint+'/versions/loader/'+game, category='loader-metadata')
                    for row in rows:
                        supported.setdefault(row['loader']['version'], []).append(game)
                releases = [dict(id=v,version_number=v,game_versions=supported.get(v, [])) for v in reversed(versions)]
            else:
                for version in reversed(versions):
                    if family == 'forge' or base.endswith('/forge/'):
                        support = version.split('-')[0]
                    else:
                        parts = version.split('.')
                        # NeoForge's documented pre-26 versioning removes Minecraft's leading "1.".
                        support = ('1.' if int(parts[0]) < 26 else '') + '.'.join(parts[:2]) if re.match(r'^\d+\.\d+\.',version) else ''
                        if support.endswith('.0') and support not in games:
                            support = support[:-2]
                    releases.append(dict(id=version,version_number=version,game_versions=[support]))
            chosen = compatible_releases(releases,games,rules,blocked=lambda r: (family,r['version_number']) in {(b['family'],b['version']) for b in self.policy['blocks']})
            chosen_ids = {r['version_number'] for r in chosen}
            blocked_ids = {r['version'] for r in self.policy['blocks'] if r['family']==family}
            # Keep observed identities for stopped versions as well as the selected download set.
            for release in releases:
                version = release['version_number']
                if version not in chosen_ids | blocked_ids:
                    continue
                artifact = urlsplit(base).path.rstrip("/").rsplit("/", 1)[-1]
                suffix = "-installer" if family in ("forge", "neoforge") else ""
                url = f"{base}{version}/{artifact}-{version}{suffix}.jar"
                if not allowed_url(url):
                    continue
                await self.mirror.db.execute(
                    "INSERT INTO runtime_files VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET game=excluded.game,updated=excluded.updated",
                    (url, family, version, "", ', '.join(release['game_versions']), family, None, "", time.time()),
                )
                if version not in chosen_ids:
                    continue
                selected.append(
                    dict(
                        url=url,
                        path=route_for(url),
                        category="loaders",
                        priority=0,
                        size=None,
                        sha1=None,
                    )
                )
        await self.mirror.db.commit()
        for family in settings["runtime_prefetch"]["cores"]:
            versions = await self.mirror.resources.compatible('modrinth',CORES[family][1],games,rules,CORES[family][0],'mod')
            for release in versions:
                item = self.mirror.resources.file(
                    "modrinth", CORES[family][1], release, CORES[family][0], "mod", False
                )
                if item:
                    selected.append(
                        dict(item, category="mods", path=route_for(item["url"]), priority=0)
                    )
        async with self.lock:
            await self.rebuild_policy()
        unique = {}
        for item in selected:
            if (not self.is_blocked(item['url']) and settings['policies'][item['category']] in ('cache','backup')):
                unique.setdefault(item.get('sha1') or item['url'], item)
        return list(unique.values())

    def is_blocked(self, url):
        version = re.search(r"/data/[^/]+/versions/([^/]+)/", url)
        return (
            url in self.policy["urls"]
            or bool(version and version[1] in self.policy["version_ids"])
            or loader_identity(url) in {(r["family"], r["version"]) for r in self.policy["blocks"]}
        )

    async def metadata(self, url):
        """Filter mutable loader lists before any cloud/mapped-source redirect."""
        path = urlsplit(url).path
        family = next(
            (key for key in LOADERS for base in loader_bases(key) if url == base + "maven-metadata.xml"), None
        )
        if (
            not family
            and ("meta.fabricmc.net" in url or "meta.quiltmc.org" in url)
            and "/versions/loader" in path
        ):
            family = "fabric" if "fabricmc" in url else "quilt"
        blocked = {r["version"] for r in self.policy["blocks"] if r["family"] == family}
        if not blocked:
            return None
        async with self.metadata_lock:
            data = await Inventory(self.mirror.root).fetch_json(
                self.mirror.http,
                url,
                category="loader-metadata",
                decode=ET.fromstring if path.endswith(".xml") else json.loads,
            )
        if path.endswith(".xml"):
            versions = data.find("./versioning/versions")
            if versions is not None:
                for child in list(versions):
                    if child.text in blocked:
                        versions.remove(child)
                remaining = [child.text for child in versions]
                for tag in ("latest", "release"):
                    element = data.find("./versioning/" + tag)
                    if element is not None and element.text in blocked:
                        element.text = remaining[-1] if remaining else ""
            return web.Response(
                body=ET.tostring(data, encoding="utf-8"),
                content_type="application/xml",
                headers={"Cache-Control": "no-store"},
            )
        if isinstance(data, list):
            data = [row for row in data if (row.get("loader", row).get("version") not in blocked)]
        return web.json_response(data, headers={"Cache-Control": "no-store"})
