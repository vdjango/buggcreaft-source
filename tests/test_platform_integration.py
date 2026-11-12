"""Real migrated account/session service -> FastAPI authorization, no upstream calls."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from aiohttp.test_utils import TestClient, TestServer
from alembic import command
from alembic.config import Config as AlembicConfig

from bugg_source.api import create_app
from bugg_source.config import Settings
from mc_translate.api import create_app as create_platform
from mc_translate.config import Config


class PlatformIntegration(unittest.IsolatedAsyncioTestCase):
    def test_remote_mirror_mode_does_not_start_another_cache_owner(self):
        from unittest.mock import MagicMock

        from bugg_source.platform import run_platform

        process = MagicMock()
        process.poll.return_value = 0
        with patch.dict(os.environ, {"TRANSLATION_MIRROR_CONTROL_PORT": "18460"}, clear=True):
            with patch("bugg_source.platform.subprocess.Popen", return_value=process) as spawn:
                self.assertEqual(run_platform(workers=0), 1)
        commands = [call.args[0] for call in spawn.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertFalse(any("mc_translate.mirror" in command for command in commands))

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.settings = Settings(
            database_url=f"sqlite:///{root / 'facts.sqlite3'}",
            data_dir=root / "facts",
            platform_auth_token="test-internal",
        )
        self.environment = patch.dict(
            os.environ,
            {
                "BUGG_PLATFORM_AUTH_TOKEN": "test-internal",
                "BUGG_DATABASE_URL": self.settings.database_url,
            },
        )
        self.environment.start()
        command.upgrade(AlembicConfig("alembic.ini"), "head")
        self.platform = TestClient(
            TestServer(
                await create_platform(
                    Config(
                        database=str(root / "platform" / "translations.sqlite3"),
                        mirror_root=str(root / "mirror"),
                        admin_token="test-login",
                    ),
                    workers=False,
                )
            )
        )
        await self.platform.start_server()
        from dataclasses import replace

        self.settings = replace(self.settings, platform_auth_url=str(self.platform.make_url("")))
        self.app = create_app(self.settings)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        await self.platform.close()
        self.environment.stop()
        self.temporary.cleanup()

    async def test_account_session_authorizes_facts_and_logout_revokes_immediately(self):
        self.assertEqual((await self.client.get("/api/v1/artifacts")).status_code, 401)
        login = await self.platform.post(
            "/v1/admin/session", headers={"Authorization": "Bearer test-login"}
        )
        self.assertEqual(login.status, 200)
        cookie = login.cookies["translation_admin"].value
        self.client.cookies.set("translation_admin", cookie)
        self.assertEqual((await self.client.get("/api/v1/artifacts")).status_code, 200)
        body = {"url": "https://cdn.modrinth.com/fixture.jar"}
        self.assertEqual(
            (
                await self.client.post(
                    "/api/v1/collections", json=body, headers={"Origin": "https://attacker.example"}
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            (await self.client.post("/api/v1/collections", json=body)).status_code, 403
        )
        self.assertEqual(
            (
                await self.client.post(
                    "/api/v1/collections", json=body, headers={"Origin": "http://testserver"}
                )
            ).status_code,
            202,
        )
        self.assertEqual(
            (
                await self.client.get(
                    "/api/v1/artifacts", headers={"Authorization": "Bearer incorrect"}
                )
            ).status_code,
            401,
        )
        self.assertEqual((await self.platform.get("/_internal/admin-session")).status, 401)
        await self.platform.delete(
            "/v1/admin/session", headers={"Cookie": "translation_admin=" + cookie}
        )
        self.assertEqual((await self.client.get("/api/v1/artifacts")).status_code, 401)

    def test_unconfigured_model_and_local_paths_are_safe_defaults(self):
        config = Config()
        self.assertFalse(config.api_key)
        self.assertFalse(config.base_url)
        self.assertFalse(config.trusted_proxy)
        self.assertTrue(config.mirror_root.startswith("data/platform/"))
        self.assertEqual(config.mirror_port, 18450)
        with self.assertRaisesRegex(ValueError, "loopback"):
            Settings(platform_auth_url="https://attacker.example")
