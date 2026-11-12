"""Launcher filters pass through the real HTTP stack without local post-filtering."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from multidict import MultiDict

from mc_translate.api import create_app
from mc_translate.catalog_gateway import arguments
from mc_translate.config import Config
from mc_translate.sources import SourceError


class CatalogGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.active, self.peak = 0, 0
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        (Path(self.temp.name) / "installation.json").write_text('{"complete":true,"sections":[]}')

        async def upstream(request):
            self.calls.append((request.path, dict(request.query), request.headers.get("x-api-key")))
            if request.method == "POST":
                body = await request.json()
                field = "modIds" if request.path == "/cf/mods" else "fileIds"
                return web.json_response({"data": [{"id": value} for value in body[field]]})
            if request.path in ("/mr/project/Large/version", "/mr/project/TooLarge/version"):
                size = 2 if "/Large/" in request.path else 9
                return web.json_response([{"id": "version", "body": "x" * (size * 1024 * 1024)}])
            if request.query.get("searchFilter") == "unavailable":
                return web.Response(status=403, text="private-test-key")
            if request.query.get("searchFilter") == "slow":
                self.active += 1
                self.peak = max(self.peak, self.active)
                try:
                    await asyncio.sleep(0.05)
                finally:
                    self.active -= 1
            if request.path.startswith("/cf"):
                return web.json_response(
                    {
                        "data": [
                            {
                                "id": 10,
                                "gameId": 432,
                                "classId": int(request.query.get("classId", "6")),
                                "name": "Fixture",
                            }
                        ],
                        "pagination": {"totalCount": 73},
                    }
                )
            facets = json.loads(request.query.get("facets", "[]"))
            kind = next(
                (
                    item.split(":", 1)[1]
                    for group in facets
                    for item in group
                    if item.startswith("project_type:")
                ),
                "mod",
            )
            return web.json_response(
                {"hits": [{"project_id": "Fixture", "project_type": kind}], "total_hits": 73}
            )

        app = web.Application()
        app.router.add_route("*", "/{route:.*}", upstream)
        self.upstream = TestServer(app)
        await self.upstream.start_server()
        self.addAsyncCleanup(self.upstream.close)
        self.config = Config(
            database=str(Path(self.temp.name) / "test.sqlite"),
            mirror_root=str(Path(self.temp.name) / "mirror"),
            admin_token="local-test",
            curseforge_key="private-test-key",
            curseforge_url=str(self.upstream.make_url("/cf")),
            modrinth_url=str(self.upstream.make_url("/mr")),
        )
        self.app = await create_app(self.config, workers=False)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_shader_runtime_registry_uses_candidate_identity_not_static_downloads(self):
        response = await self.client.get("/v1/catalog/shader-runtimes")
        self.assertEqual(response.status, 200)
        manifest = await response.json()
        self.assertEqual(manifest["schema"], "bugg-shader-support-v1")
        self.assertEqual(
            [(item["id"], item["projectId"]) for item in manifest["runtimes"]],
            [("iris", "YL57xq9U"), ("oculus", "GchcoXML")],
        )
        for item in manifest["runtimes"]:
            self.assertEqual(item["publisher"], "Modrinth")
            self.assertTrue(item["markers"])
            self.assertNotIn("url", item)
            self.assertNotIn("gameVersions", item)
        self.assertEqual(self.calls, [])
        rules = manifest["compatibilityRules"]
        self.assertEqual(manifest["runtimes"][0]["curseforgeProjectId"], "455508")
        self.assertEqual({r["versionId"] for r in rules}, {"VHtnT9Q4", "Ag5S66CF"})
        for rule in rules:
            self.assertEqual(rule["gameVersion"], "26.3")
            self.assertEqual(rule["loader"], "neoforge")
            self.assertEqual(rule["minLoaderVersion"], "26.3.0.65")
            self.assertTrue(rule["reason"])
            self.assertEqual(rule["reference"], "https://github.com/CaffeineMC/sodium/pull/3948")
            self.assertNotIn("replacementVersionId", rule)
        response = await self.client.get("/v1/catalog/shader-runtimes?url=https://example.org")
        self.assertEqual(response.status, 400)

    async def test_official_bulk_metadata_validates_body_and_caches_each_batch(self):
        for route, field in (("mods", "modIds"), ("mods/files", "fileIds")):
            for ids in ([11, 12], [11, 12], [13]):
                response = await self.client.post(
                    "/v1/catalog/curseforge/" + route, json={field: ids}
                )
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["data"], [{"id": v} for v in ids])
                self.assertNotIn("private-test-key", await response.text())
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(call[2] == "private-test-key" for call in self.calls))
        for body in ({"modIds": []}, {"modIds": [True]}, {"modIds": [-1]},
                     {"modIds": [1] * 1001}, {"fileIds": [1]},
                     {"modIds": [1], "url": "https://example.org"}):
            response = await self.client.post("/v1/catalog/curseforge/mods", json=body)
            self.assertEqual(response.status, 400)
        response = await self.client.post("/v1/catalog/modrinth/mods", json={"modIds": [1]})
        self.assertEqual(response.status, 405)
        response = await self.client.post("/v1/catalog/curseforge/mods/search", json={"modIds": [1]})
        self.assertEqual(response.status, 405)
        self.assertEqual(len(self.calls), 4)

    async def test_four_content_classes_forward_intersecting_official_filters_and_pagination(self):
        for class_id in (6, 12, 6552, 4471):
            params = {
                "gameId": "432",
                "classId": str(class_id),
                "categoryId": "407",
                "gameVersion": "1.21.1",
                "searchFilter": "中文 test",
                "pageSize": "20",
                "index": "20",
                "sortField": "2",
                "sortOrder": "desc",
            }
            if class_id in (6, 4471):
                params["modLoaderType"] = "4"
            for _ in range(2):
                response = await self.client.get(
                    "/v1/catalog/curseforge/mods/search", params=params
                )
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["pagination"]["totalCount"], 73)
                self.assertNotIn("private-test-key", await response.text())
            self.assertEqual(self.calls[-1], ("/cf/mods/search", params, "private-test-key"))
        self.assertEqual(len(self.calls), 4)

    async def test_official_arrays_are_preserved_and_cache_keys_include_every_filter(self):
        base = {
            "gameId": "432",
            "classId": "6",
            "categoryId": "406",
            "categoryIds": "[407,409]",
            "gameVersions": '["1.20.1","1.21.1"]',
            "modLoaderTypes": "[1,4]",
            "authorId": "2147483647",
            "primaryAuthorId": "100",
            "gameVersionTypeId": "1",
            "slug": "example",
        }
        for change in ({}, {"categoryIds": "[408]"}, {"index": "50"}):
            params = base | change
            response = await self.client.get("/v1/catalog/curseforge/mods/search", params=params)
            self.assertEqual(response.status, 200)
            self.assertEqual(self.calls[-1][1], params)
        self.assertEqual(len(self.calls), 3)

    async def test_modrinth_facets_keep_or_groups_and_cross_group_and_for_all_kinds(self):
        for kind in ("mod", "resourcepack", "shader", "modpack"):
            facets = [
                ["project_type:" + kind],
                ["versions:1.21.1"],
                ["categories:technology", "categories:adventure"],
            ]
            if kind in ("mod", "modpack"):
                facets.append(["categories:fabric"])
            params = {
                "query": "中文",
                "facets": json.dumps(facets),
                "index": "downloads",
                "offset": "20",
                "limit": "20",
            }
            response = await self.client.get("/v1/catalog/modrinth/search", params=params)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["total_hits"], 73)
            self.assertEqual(self.calls[-1], ("/mr/search", params, None))

    async def test_large_version_metadata_has_bounded_catalog_budget(self):
        response = await self.client.get("/v1/catalog/modrinth/project/Large/version")
        self.assertEqual(response.status, 200)
        self.assertEqual(len((await response.json())[0]["body"]), 2 * 1024 * 1024)
        with self.assertRaises(SourceError):
            await self.app["engine"].sources.get(
                self.upstream.make_url("/mr/project/Large/version")
            )
        response = await self.client.get("/v1/catalog/modrinth/project/TooLarge/version")
        self.assertEqual(response.status, 503)
        self.assertEqual(len(self.app["catalog_gateway"].cache), 1)
        for index in range(17):
            response = await self.client.get(
                "/v1/catalog/modrinth/project/Large/version",
                params={"game_versions": json.dumps([f"1.{index}"])},
            )
            self.assertEqual(response.status, 200)
            await response.read()
        gateway = self.app["catalog_gateway"]
        self.assertLessEqual(gateway.cache_bytes, 32 * 1024 * 1024)
        self.assertLess(len(gateway.cache), 17)

    async def test_modrinth_sha1_lookup_uses_same_gateway_and_transport(self):
        digest = "a" * 40
        response = await self.client.get("/v1/catalog/modrinth/version_file/" + digest)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.calls[-1], ("/mr/version_file/" + digest, {}, None))
        for value in ("x" * 40, "a" * 39, "a" * 41):
            response = await self.client.get("/v1/catalog/modrinth/version_file/" + value)
            self.assertEqual(response.status, 404)
        self.assertEqual(len(self.calls), 1)

    async def test_invalid_parameters_methods_and_routes_never_reach_upstream(self):
        base = {"gameId": "432", "classId": "6"}
        for bad in (
            {"categoryIds": "[true]"},
            {"categoryIds": "[]"},
            {"categoryIds": "[" + ",".join(["1"] * 11) + "]"},
            {"categoryId": "-1"},
            {"categoryId": "2147483648"},
            {"gameVersions": '["1","2","3","4","5"]'},
            {"modLoaderTypes": "[7]"},
            {"index": "9990", "pageSize": "20"},
            {"classId": "999"},
            {"gameId": "1"},
            {"url": "https://other.example"},
        ):
            response = await self.client.get(
                "/v1/catalog/curseforge/mods/search", params=base | bad
            )
            self.assertEqual(response.status, 400)
        for path in ("/v1/catalog/curseforge/users", "/v1/catalog/modrinth/user/test"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 404)
        response = await self.client.post("/v1/catalog/curseforge/mods/search", json=base)
        self.assertEqual(response.status, 405)
        with self.assertRaises(ValueError):
            arguments(
                "curseforge",
                "/mods/search",
                MultiDict(list(base.items()) + [("categoryId", "1"), ("categoryId", "2")]),
            )
        self.assertEqual(self.calls, [])

    async def test_metadata_transport_and_browser_credential_isolation(
        self,
    ):
        for provider, route, params in (
            ("curseforge", "/categories", {"gameId": "432"}),
            ("curseforge", "/mods/10/description", {}),
            ("curseforge", "/mods/10/files", {"gameVersion": "1.21.1", "modLoaderType": "4"}),
            ("modrinth", "/tag/category", {}),
            (
                "modrinth",
                "/project/Fixture/version",
                {"game_versions": '["1.21.1"]', "loaders": '["fabric"]'},
            ),
            ("modrinth", "/version/Fixture", {}),
        ):
            response = await self.client.get(
                "/v1/catalog/" + provider + route,
                params=params,
                headers={"x-api-key": "browser-key", "Cookie": "private-cookie"},
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(self.calls[-1][1], params)
            self.assertEqual(
                self.calls[-1][2], "private-test-key" if provider == "curseforge" else None
            )
        self.assertIs(self.app["catalog_gateway"].sources, self.app["engine"].sources)
        for i in range(40):
            await self.client.get("/v1/catalog/modrinth/search", params={"query": str(i)})
        self.assertEqual(len(self.app["catalog_gateway"].cache), 32)

    async def test_missing_authorization_and_provider_failure_are_explicit_and_not_cached(self):
        base = {"gameId": "432", "classId": "6", "searchFilter": "unavailable"}
        for _ in range(2):
            response = await self.client.get("/v1/catalog/curseforge/mods/search", params=base)
            self.assertEqual(response.status, 503)
            self.assertNotIn("private-test-key", await response.text())
        self.assertEqual(len(self.calls), 2)
        self.config.curseforge_key = ""
        response = await self.client.get(
            "/v1/catalog/curseforge/mods/search", params=base | {"searchFilter": "other"}
        )
        self.assertEqual(response.status, 503)
        self.assertEqual(len(self.calls), 2)

    async def test_native_launcher_against_running_source_gateway(self):
        executable = os.environ.get("BUGG_TEST_LAUNCHER_CONTENT_EXE")
        if not executable:
            self.skipTest("Explicit native launcher integration only")
        env = dict(
            os.environ,
            BUGG_TEST_CONTENT_GATEWAY_ORIGIN=str(self.client.make_url("/")).rstrip("/"),
            QT_QUICK_BACKEND="software",
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            "native_client_queries_new_source_gateway",
            "-o",
            "-,txt",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        self.assertEqual(process.returncode, 0, output.decode("utf-8", errors="replace"))
        self.assertEqual(len(self.calls), 8)
        for _, params, _ in self.calls[:4]:
            self.assertEqual(params["categoryId"], "407")
            self.assertEqual(params["index"], "20")
            self.assertEqual(params["gameVersion"], "1.21.1")
            self.assertEqual("modLoaderType" in params, params["classId"] in ("6", "4471"))

    async def test_catalog_queries_share_existing_source_capacity(self):
        responses = await asyncio.gather(
            *(
                self.client.get(
                    "/v1/catalog/curseforge/mods/search",
                    params={
                        "gameId": "432",
                        "classId": "6",
                        "categoryId": str(i + 1),
                        "searchFilter": "slow",
                    },
                )
                for i in range(10)
            )
        )
        self.assertTrue(all(response.status == 200 for response in responses))
        self.assertGreater(self.peak, 1)
        self.assertLessEqual(self.peak, 4)
        self.assertEqual(self.active, 0)
