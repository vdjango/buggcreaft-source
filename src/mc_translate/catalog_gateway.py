"""Official launcher catalogue filters; transport and capacity belong to Sources."""

import json
import re
import time
from collections import OrderedDict
from importlib.resources import files

from aiohttp import web
from multidict import MultiMapping
from yarl import URL

from .sources import SourceError, Sources
from .runtime_catalog import CORES, blocked_release

CLASSES = {6, 12, 6552, 4471}
CF_ID = r"[1-9][0-9]{0,9}"
MR_ID = r"[A-Za-z0-9_-]{1,96}"
CF_ROUTE = re.compile(rf"/mods/{CF_ID}(?:/description|/files(?:/{CF_ID})?)?")
MR_ROUTE = re.compile(
    rf"/(?:project/{MR_ID}(?:/version)?|version/{MR_ID}|version_file/[0-9a-f]{{40}})"
)


def integer(value: str, low: int, high: int) -> int:
    """Validate official integer query fields without accepting float/bool aliases."""
    if not value.isascii() or not value.isdigit() or len(value) > 10:
        raise ValueError("invalid_integer")
    result = int(value)
    if not low <= result <= high:
        raise ValueError("integer_out_of_range")
    return result


def array(value: str, limit: int, item_type: type, low: int = 1, high: int = 2**31 - 1) -> list:
    items = json.loads(value)
    if not isinstance(items, list) or not 1 <= len(items) <= limit:
        raise ValueError("invalid_array")
    for item in items:
        if type(item) is not item_type:
            raise ValueError("invalid_array_item")
        if item_type is int:
            integer(str(item), low, high)
        elif not item or len(item) > 96 or any(ord(c) < 32 for c in item):
            raise ValueError("invalid_array_string")
    return items


def arguments(provider: str, route: str, query: MultiMapping[str]) -> dict[str, str]:
    """Keep official parameter spellings/JSON; do not implement filtering locally."""
    params = dict(query)
    if len(params) != len(query) or sum(len(k) + len(v) for k, v in params.items()) > 4096:
        raise ValueError("invalid_query")
    allowed: set[str] = set()
    if provider == "curseforge":
        if route == "/categories":
            allowed = {"gameId", "classId"}
        elif route == "/mods/search":
            allowed = {
                "gameId",
                "classId",
                "categoryId",
                "categoryIds",
                "gameVersion",
                "gameVersions",
                "modLoaderType",
                "modLoaderTypes",
                "gameVersionTypeId",
                "authorId",
                "primaryAuthorId",
                "slug",
                "searchFilter",
                "sortField",
                "sortOrder",
                "index",
                "pageSize",
            }
        elif CF_ROUTE.fullmatch(route):
            if route.endswith("/files"):
                allowed = {"gameVersion", "modLoaderType", "gameVersionTypeId", "index", "pageSize"}
        else:
            raise web.HTTPNotFound()
        if route in ("/categories", "/mods/search") and params.get("gameId") != "432":
            raise ValueError("minecraft_only")
        if "classId" in params and integer(params["classId"], 1, 2**31 - 1) not in CLASSES:
            raise ValueError("unsupported_class")
        for key, value in params.items():
            if key in {"categoryIds", "gameVersions", "modLoaderTypes"}:
                count, kind = {
                    "categoryIds": (10, int),
                    "gameVersions": (4, str),
                    "modLoaderTypes": (5, int),
                }[key]
                array(
                    value,
                    count,
                    kind,
                    0 if key == "modLoaderTypes" else 1,
                    6 if key == "modLoaderTypes" else 2**31 - 1,
                )
            elif key == "sortOrder":
                if value not in ("asc", "desc"):
                    raise ValueError("invalid_sort")
            elif key in {"gameVersion", "slug", "searchFilter"}:
                if len(value) > (200 if key == "searchFilter" else 96) or any(
                    ord(c) < 32 for c in value
                ):
                    raise ValueError("invalid_text")
            else:
                integer(
                    value,
                    0 if key in ("index", "modLoaderType") else 1,
                    6 if key == "modLoaderType" else 12 if key == "sortField" else 2**31 - 1,
                )
        page_size = integer(params.get("pageSize", "50"), 1, 50)
        if integer(params.get("index", "0"), 0, 10000) + page_size > 10000:
            raise ValueError("pagination_limit")
    elif provider == "modrinth":
        if route == "/search":
            allowed = {"query", "facets", "index", "offset", "limit"}
        elif route in ("/tag/category", "/tag/loader", "/tag/game_version"):
            pass
        elif MR_ROUTE.fullmatch(route):
            if route.endswith("/version") and route.startswith("/project/"):
                allowed = {"game_versions", "loaders", "featured"}
        else:
            raise web.HTTPNotFound()
        for key, value in params.items():
            if key == "facets":
                groups = json.loads(value)
                if not isinstance(groups, list) or len(groups) > 16:
                    raise ValueError("invalid_facets")
                for group in groups:
                    if (
                        not isinstance(group, list)
                        or not 1 <= len(group) <= 32
                        or any(
                            not isinstance(item, str)
                            or not 1 <= len(item) <= 200
                            or ":" not in item
                            for item in group
                        )
                    ):
                        raise ValueError("invalid_facet")
            elif key in ("game_versions", "loaders"):
                array(value, 32, str)
            elif key == "index":
                if value not in ("relevance", "downloads", "follows", "newest", "updated"):
                    raise ValueError("invalid_sort")
            elif key == "offset":
                integer(value, 0, 2**31 - 1)
            elif key == "limit":
                integer(value, 1, 100)
            elif key == "featured":
                if value not in ("true", "false"):
                    raise ValueError("invalid_featured")
            elif key == "query" and len(value) > 200:
                raise ValueError("query_too_long")
    else:
        raise web.HTTPNotFound()
    if set(params) - allowed:
        raise ValueError("unknown_parameter")
    return params


class CatalogGateway:
    def __init__(self, app: web.Application, sources: Sources, mirror_control=None):
        self.app, self.sources = app, sources
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.mirror_control = mirror_control

    async def response(self, value, provider, *, observe=False):
        payload = value.get('data') if provider == 'curseforge' and isinstance(value, dict) else value
        releases = payload if isinstance(payload, list) else [payload]
        if self.mirror_control:
            # A cache hit is still a real request. Only JAR release records are
            # admitted; search projects do not cause arbitrary binary downloads.
            candidates = [row for row in releases if isinstance(row, dict) and
                          ('files' in row and 'project_id' in row or
                           'hashes' in row and 'modId' in row)][:100]
            if candidates:
                await self.mirror_control.call(dict(action='dependency_observe', provider=provider, releases=candidates))
        cores = [row for row in releases if isinstance(row, dict) and row.get('project_id') in {project for _, project in CORES.values()} and 'files' in row]
        hashed = provider=='curseforge' and any(isinstance(row, dict) and isinstance(row.get('hashes'), list) for row in releases)
        if (not cores and not hashed) or not self.mirror_control:
            return web.json_response(value)
        if observe and cores:
            observations, observation_bytes = [], 0
            for row in cores[:2000]:
                files = row.get('files', [])
                primary = next((f for f in files if f.get('primary')), files[0] if files else {})
                observation={**{k:row.get(k) for k in ('project_id','version_number','id','game_versions','loaders')},
                    'files':[{k:primary.get(k) for k in ('url','size','hashes')}]}
                size = len(json.dumps(observation).encode())
                if observation_bytes+size>1800000:
                    break
                observations.append(observation)
                observation_bytes += size
            policy = await self.mirror_control.call(dict(action='runtime_observe', releases=observations))
        else:
            policy = await self.mirror_control.call(dict(action='runtime_policy'))
        if policy is None:
            return web.json_response(dict(code='runtime_policy_unavailable', message='核心分发规则暂时不可读取，请稍后重试。'), status=503)
        if isinstance(payload, list):
            filtered=[row for row in payload if not blocked_release(row, policy)]
            return web.json_response({**value,'data':filtered} if provider=='curseforge' else filtered, headers={'Cache-Control':'no-store'})
        if blocked_release(payload, policy):
            return web.json_response(dict(code='runtime_blocked',message='此核心版本或其必要依赖已暂停分发，请选择其他兼容版本。'),status=403,headers={'Cache-Control':'no-store'})
        return web.json_response(value, headers={'Cache-Control':'no-store'})

    async def shader_runtimes(self, request: web.Request) -> web.Response:
        """Publisher identities and evidenced exceptions to incomplete release tags."""
        self.app["api"].rate(request, "catalog", 120)
        if request.query:
            raise ValueError("unknown_parameter")
        manifest = json.loads(files("mc_translate").joinpath("contracts/shader-runtimes.json").read_text(encoding="utf-8"))
        return web.json_response(manifest, headers={"Cache-Control": "no-store"})

    async def read(self, request: web.Request) -> web.Response:
        self.app["api"].rate(request, "catalog", 120)
        provider, route = request.match_info["provider"], "/" + request.match_info["route"]
        body = None
        if request.method == "POST":
            field = {"/mods": "modIds", "/mods/files": "fileIds"}.get(route)
            if provider != "curseforge" or not field:
                raise web.HTTPNotFound()
            if request.query:
                raise ValueError("unknown_parameter")
            if request.content_length is None or request.content_length > 65536:
                raise ValueError("invalid_catalog_body")
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {field}:
                raise ValueError("invalid_catalog_body")
            body = {field: array(json.dumps(body[field]), 1000, int)}
            params = {}
        else:
            params = arguments(provider, route, request.query)
        config = self.sources.config
        if provider == "curseforge" and not config.curseforge_key:
            return web.json_response({"code": "catalog_authorization_unavailable"}, status=503)
        key = (request.method, provider, route, tuple(sorted(params.items())),
               json.dumps(body, sort_keys=True, separators=(",", ":")))
        cached = self.cache.get(key)
        if cached and time.monotonic() < cached[0]:
            return await self.response(cached[1], provider)
        base = config.curseforge_url if provider == "curseforge" else config.modrinth_url
        headers = {"x-api-key": config.curseforge_key} if provider == "curseforge" else None
        try:
            async with self.sources.semaphore:
                cached = self.cache.get(key)
                if cached and time.monotonic() < cached[0]:
                    return await self.response(cached[1], provider)
                value = await self.sources.get(
                    URL(base.rstrip("/") + route).with_query(params), headers,
                    limit=8 * 1024 * 1024, **({} if body is None else {"body": body})
                )
        except SourceError as exc:
            return web.json_response(
                {"code": exc.code}, status=404 if exc.code == "source_not_found" else
                429 if exc.code == "source_rate_limited" else 503
            )
        if not isinstance(value, (dict, list)):
            return web.json_response({"code": "invalid_catalog_response"}, status=503)
        ttl = 60 if route in ("/search", "/mods/search") else 300
        response = web.json_response(value)
        if key in self.cache:
            self.cache_bytes -= self.cache[key][2]
        size = len(response.body)
        self.cache[key] = (time.monotonic() + ttl, value, size)
        self.cache_bytes += size
        self.cache.move_to_end(key)
        while len(self.cache) > 32 or self.cache_bytes > 32 * 1024 * 1024:
            self.cache_bytes -= self.cache.popitem(last=False)[1][2]
        return await self.response(value, provider, observe=True)
