"""Official popular/pinned file discovery; all bytes use the existing mirror job."""
import asyncio
import hashlib
import json
import re
import time
from urllib.parse import quote

from yarl import URL

from .mirror_catalog import allowed_url, route_for, write_json
from .sources import Sources, SourceError

TYPES = ('mod', 'resourcepack', 'shader', 'modpack')
PROVIDERS = ('modrinth', 'curseforge')
DEFAULTS = dict(enabled=False, limit=1000, types=['mod'], providers=['modrinth'],
                refresh_hours=24, pins=[], compatibility=dict(mode='recent', recent=3, games=[], per_game=5, overrides={}))
CLASSES = dict(mod=6, resourcepack=12, shader=6552, modpack=4471)


def validate_compatibility(value):
    result = {**DEFAULTS['compatibility'], **value} if isinstance(value, dict) else {}
    game_id = r'[A-Za-z0-9_.+ -]{1,64}'
    if (set(result) != set(DEFAULTS['compatibility']) or result.get('mode') not in ('recent', 'selected', 'all')
            or type(result.get('recent')) is not int or not 1 <= result['recent'] <= 200
            or type(result.get('per_game')) is not int or not 0 <= result['per_game'] <= 200
            or not isinstance(result.get('games'), list) or len(result['games']) > 200
            or any(not isinstance(v, str) or not re.fullmatch(game_id, v) for v in result['games'])
            or result['mode'] == 'selected' and not result['games']
            or not isinstance(result.get('overrides'), dict) or len(result['overrides']) > 200
            or any(not re.fullmatch(game_id, k) or type(v) is not int or not 0 <= v <= 200 for k, v in result['overrides'].items())):
        raise ValueError('请选择游戏范围；每个游戏版本的缓存数量为 0–200，游戏版本及例外最多 200 条')
    return dict(result, games=list(dict.fromkeys(result['games'])))


def compatible_releases(releases, games, rules, *, blocked=lambda release: False, valid=lambda release: True):
    """Newest compatible releases per game; a shared release consumes one slot in each game, one file globally."""
    counts, selected, seen = dict.fromkeys(games, 0), [], {}
    for release in sorted(releases, key=lambda r: r.get('date_published', r.get('fileDate', '')), reverse=True):
        if blocked(release) or not valid(release):
            continue
        files = release.get('files', [])
        primary = next((f for f in files if f.get('primary')), files[0] if files else {})
        key = primary.get('hashes', {}).get('sha1') or next((h.get('value') for h in release.get('hashes', []) if h.get('algo') == 1), None) or release.get('id')
        supported = set(release.get('game_versions', release.get('gameVersions', [])))
        covered = [game for game in games if game in supported and game not in seen.get(key,set()) and counts[game] < rules['overrides'].get(game, rules['per_game'])]
        if covered:
            if key not in seen:
                selected.append(release)
            seen.setdefault(key,set()).update(covered)
            for game in covered:
                counts[game] += 1
    return selected


def validate_resource_prefetch(value):
    if not isinstance(value, dict) or set(value) - DEFAULTS.keys():
        raise ValueError('资源预缓存设置无效')
    result = {**DEFAULTS, **value}
    result['compatibility'] = validate_compatibility(result['compatibility'])
    if (type(result['enabled']) is not bool or type(result['limit']) is not int
            or not 0 <= result['limit'] <= 10000 or type(result['refresh_hours']) is not int
            or not 1 <= result['refresh_hours'] <= 720):
        raise ValueError('热门数量为 0–10000，更新间隔为 1–720 小时')
    for key, choices in (('types', TYPES), ('providers', PROVIDERS)):
        if (not isinstance(result[key], list) or not result[key]
                or any(not isinstance(item, str) or item not in choices for item in result[key])):
            raise ValueError('请选择有效的资源类别及平台')
        result[key] = list(dict.fromkeys(result[key]))
    if not isinstance(result['pins'], list) or len(result['pins']) > 500:
        raise ValueError('固定资源最多 500 条')
    pins, seen = [], set()
    for item in result['pins']:
        if (not isinstance(item, dict) or set(item) - {'provider', 'project', 'version'}
                or item.get('provider', 'modrinth') not in PROVIDERS):
            raise ValueError('固定资源格式无效')
        provider = item.get('provider', 'modrinth')
        project, version = item.get('project', ''), item.get('version', '')
        if (not isinstance(project, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', project)
                or not isinstance(version, str) or len(version) > 128
                or version and not re.fullmatch(r'[A-Za-z0-9_.+ -]+', version)
                or provider == 'curseforge' and (not project.isdigit() or version and not version.isdigit())):
            raise ValueError('请填写项目 ID/短名；CurseForge 使用数字项目及文件 ID')
        key = (provider, project, version)
        if key not in seen:
            pins.append(dict(provider=provider, project=project, version=version))
            seen.add(key)
    result['pins'] = pins
    return result


class ResourcePrefetch:
    def __init__(self, mirror):
        self.mirror = mirror
        self.path = mirror.root / 'resource-prefetch.json'
        try:
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.state = {}
        if self.state.get('pending') and self.state.get('last_state') == 'cancelled':
            self.state['next_at'] = min(self.state.get('next_at', 0), time.time() + 300)

    async def open(self):
        await self.mirror.db.executescript('''
            CREATE TABLE IF NOT EXISTS resource_prefetch_files(
                url TEXT PRIMARY KEY, provider TEXT NOT NULL, project TEXT NOT NULL,
                version TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL,
                pinned INTEGER NOT NULL, size INTEGER NOT NULL, sha1 TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS resource_prefetch_pins ON resource_prefetch_files(pinned,url);
        ''')
        await self.mirror.db.commit()

    async def protected(self, digest):
        return bool(await self.mirror.one('SELECT 1 FROM resource_prefetch_files r JOIN cache c ON c.url=r.url WHERE r.pinned=1 AND c.object=? LIMIT 1', (digest,)))

    async def get(self, provider, route, params=None, *, observe=True):
        config = self.mirror.config
        if provider == 'curseforge' and not config.curseforge_key:
            raise ValueError('CurseForge API 密钥未配置，请先配置或只选择 Modrinth')
        base = config.curseforge_url if provider == 'curseforge' else config.modrinth_url
        headers = {'x-api-key': config.curseforge_key} if provider == 'curseforge' else None
        client = Sources(config, self.mirror.http, None)
        try:
            value = await client.get(URL(base.rstrip('/') + route).with_query(params or {}), headers,
                                     limit=8 * 1024 * 1024)
            payload = value.get('data') if provider == 'curseforge' and isinstance(value, dict) else value
            rows = payload if isinstance(payload, list) else [payload]
            if observe:
                await self.mirror.facts.discovery.observe(provider, rows[:100], 'hotspot')
            return value
        except SourceError as exc:
            raise ValueError(f'{provider} 目录请求失败：{exc.code}') from None

    async def game_plan(self, rules):
        """Only official release IDs; no guessed numeric range or snapshot expansion."""
        rules = validate_compatibility(rules)
        if rules['mode'] == 'selected':
            return rules['games']
        versions = await self.get('modrinth', '/tag/game_version')
        stable = sorted((row for row in versions if row.get('version_type') == 'release'), key=lambda row: row.get('date', ''), reverse=True)
        return [row['version'] for row in stable[:rules['recent'] if rules['mode'] == 'recent' else None]]

    async def compatible(self, provider, project, games, rules, title, kind):
        """Reuse platform transport and file validation; stopped releases never consume a quota."""
        if provider == 'modrinth':
            releases = await self.get(provider, '/project/' + str(project) + '/version', observe=False)
            await self.mirror.runtimes.observe(releases)
        else:
            releases = []
            for game in games:
                offset = 0
                while offset < 10000:
                    page = await self.get(provider, f'/mods/{project}/files', dict(gameVersion=game, pageSize=50, index=offset), observe=False)
                    rows = page.get('data', [])
                    releases.extend(rows)
                    # Latest official API order; continue past blocked/invalid entries to fill the game quota.
                    chosen = compatible_releases(releases, [game], rules, blocked=self.mirror.runtimes.release_blocked,
                        valid=lambda r: self.file(provider, project, r, title, kind, False) is not None)
                    if len(chosen) >= rules['overrides'].get(game, rules['per_game']) or len(rows) < 50:
                        break
                    offset += len(rows)
                    await asyncio.sleep(.5)
        selected = compatible_releases(releases, games, rules, blocked=self.mirror.runtimes.release_blocked,
            valid=lambda r: self.file(provider, project, r, title, kind, False) is not None)
        await self.mirror.facts.discovery.observe(provider, selected[:100], 'hotspot')
        return selected

    def file(self, provider, project, version, title, kind, pinned):
        if provider == 'modrinth':
            files = version.get('files', [])
            file = next((item for item in files if item.get('primary')), files[0] if files else {})
            sha1, size, url = file.get('hashes', {}).get('sha1', ''), file.get('size'), file.get('url', '')
        else:
            file = version
            sha1 = next((item.get('value', '') for item in file.get('hashes', []) if item.get('algo') == 1), '')
            size, url = file.get('fileLength'), file.get('downloadUrl') or ''
        if (not allowed_url(url) or not re.fullmatch('[0-9a-f]{40}', sha1)
                or type(size) is not int or size <= 0):
            if pinned:
                raise ValueError(f'{title} 没有允许分发且带校验值的主文件')
            return None
        # Mirror routes canonicalize encoded '+' to literal '+'. Use one URL key.
        url = url.replace('%2B', '+').replace('%2b', '+')
        return dict(url=url, provider=provider, project=str(project), version=str(version['id']),
                    title=str(title)[:200], kind=kind, pinned=int(pinned), size=size, sha1=sha1)

    async def discover(self, settings):
        selected = validate_resource_prefetch(settings)
        games = await self.game_plan(selected['compatibility'])
        signature = hashlib.sha256(json.dumps(dict(settings=selected,games=games,blocks=self.mirror.runtimes.policy['blocks']), sort_keys=True).encode()).hexdigest()
        if self.state.get('pending') and self.state.get('signature') == signature:
            async with self.mirror.db.execute('SELECT * FROM resource_prefetch_files') as cursor:
                rows = await cursor.fetchall()
            self.mirror.job_state.update(discovered=len(rows), resumed=True)
            return [dict(row, category='mods', priority=0, path=route_for(row['url'])) for row in rows]
        files = {}
        job = self.mirror.job_state
        job.update(phase='resource_catalogue', discovered=0, projects=0, errors=0)
        for pin in selected['pins']:
            provider, project, version = pin['provider'], pin['project'], pin['version']
            meta = await self.get(provider, '/project/' + project if provider == 'modrinth' else '/mods/' + project)
            if provider == 'modrinth':
                project_id = meta.get('id')
                title, kind = meta.get('title', project), meta.get('project_type', 'mod')
            else:
                meta = meta['data']
                if meta.get('allowModDistribution') is False:
                    raise ValueError(f'{project} 的作者不允许第三方分发')
                project_id = project
                title = meta.get('name', project)
                kind = next((k for k, value in CLASSES.items() if value == meta.get('classId')), 'mod')
            if version:
                if provider == 'modrinth':
                    release = await self.get(provider, '/project/' + project + '/version/' + quote(version, safe=''))
                else:
                    release = (await self.get(provider, f'/mods/{project}/files/{version}'))['data']
                valid_project = release and (release.get('project_id') == meta.get('id') if provider == 'modrinth'
                                             else str(release.get('modId')) == str(meta['id']))
                if not valid_project:
                    raise ValueError(f'{project} 的指定版本不可用')
                candidates = [release]
            else:
                # Fixed projects share the same per-game compatibility quotas and
                # release blocklist as popular projects; a shared file is stored once.
                candidates = await self.compatible(
                    provider, project_id, games, selected['compatibility'], title, kind)
            for release in candidates:
                if self.mirror.runtimes.release_blocked(release):
                    continue
                item = self.file(provider, project_id, release, title, kind, True)
                if item:
                    files[item['url']] = item
            job['discovered'] = len(files)
            job['projects'] += 1
            await asyncio.sleep(.5)
        for provider in selected['providers']:
            for kind in selected['types']:
                offset = 0
                while offset < selected['limit']:
                    count = min(100 if provider == 'modrinth' else 50, selected['limit'] - offset)
                    if provider == 'modrinth':
                        page = await self.get(provider, '/search', dict(index='downloads', limit=count, offset=offset, facets=json.dumps([[f'project_type:{kind}']])))
                        projects = page.get('hits', [])[:count]
                        for project in projects:
                            for release in await self.compatible(provider, project['project_id'], games, selected['compatibility'], project['title'], kind):
                                item = self.file(provider, project['project_id'], release, project['title'], kind, False)
                                if item:
                                    files.setdefault(item['url'], item)
                            await asyncio.sleep(.5)
                    else:
                        page = await self.get(provider, '/mods/search', dict(gameId=432, classId=CLASSES[kind], sortField=6, sortOrder='desc', pageSize=count, index=offset))
                        projects = page.get('data', [])[:count]
                        for project in projects:
                            if project.get('allowModDistribution') is False:
                                continue
                            for release in await self.compatible(provider, project['id'], games, selected['compatibility'], project['name'], kind):
                                item = self.file(provider, project['id'], release, project['name'], kind, False)
                                if item:
                                    files.setdefault(item['url'], item)
                            await asyncio.sleep(.5)
                    job['projects'] += len(projects)
                    job['discovered'] = len(files)
                    offset += len(projects)
                    if len(projects) < count:
                        break
                    # Keep metadata requests below the official anonymous rate.
                    await asyncio.sleep(1)
        rows = list(files.values())
        async with self.mirror.space_lock:
            await self.mirror.db.execute('DELETE FROM resource_prefetch_files')
            await self.mirror.db.executemany('INSERT INTO resource_prefetch_files VALUES(:url,:provider,:project,:version,:title,:kind,:pinned,:size,:sha1)', rows)
            await self.mirror.db.commit()
        self.state.update(checked_at=time.time(), next_at=0, pending=True, signature=signature)
        await asyncio.to_thread(write_json, self.path, self.state)
        return [dict(item, category='mods', priority=0, path=route_for(item['url'])) for item in rows]

    async def finished(self, job, settings):
        state = job.get('state')
        self.state.update(pending=state not in ('finished', 'incomplete'),
            next_at=time.time() + (300 if state in ('failed', 'disk_limit', 'cancelled') else settings['refresh_hours'] * 3600),
            last_state=state)
        await asyncio.to_thread(write_json, self.path, self.state)

    async def request(self, paused=False):
        """Persist manual intent; one existing mirror worker owns execution."""
        self.state.update(paused=paused, requested=not paused, next_at=0 if not paused else self.state.get('next_at', 0))
        await asyncio.to_thread(write_json, self.path, self.state)

    async def started(self):
        self.state.update(requested=False, paused=False)
        await asyncio.to_thread(write_json, self.path, self.state)

    async def snapshot(self, offset=0):
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('资源分页无效')
        # One indexed ledger join, no directory scans or new upstream requests.
        query = '''FROM resource_prefetch_files r LEFT JOIN cache c ON c.url=r.url
            LEFT JOIN objects o ON o.digest=c.object'''
        ready = "(o.local=1 OR EXISTS(SELECT 1 FROM object_locations l WHERE l.scope='mirror' AND l.digest=c.object AND l.state='ready'))"
        total = await self.mirror.one('SELECT COUNT(*) total,COALESCE(SUM(r.pinned),0) pinned,COALESCE(SUM(r.size),0) bytes,COALESCE(SUM(CASE WHEN '+ready+' THEN 1 ELSE 0 END),0) ready '+query)
        async with self.mirror.db.execute('SELECT r.*,'+ready+' ready '+query+' ORDER BY r.pinned DESC,r.title,r.url LIMIT 50 OFFSET ?', (offset,)) as cursor:
            files = [dict(row) for row in await cursor.fetchall()]
        return dict(self.state, **dict(total), files=files, offset=offset,
                    has_more=offset+len(files) < total['total'],
                    providers=dict(modrinth=True, curseforge=bool(self.mirror.config.curseforge_key)))
