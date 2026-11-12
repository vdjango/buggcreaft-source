"""Real HTTP notifications, local-cache mode and durable replay; no public downloads."""

import asyncio
import hashlib
import unittest

import test_mirror as mirror_fixture
from aiohttp import web
from aiohttp.test_utils import TestServer

from mc_translate.cache_facts import CacheFacts


class FactsCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reuse the existing real mirror/upstream fixture without inheriting its tests.
        await mirror_fixture.MirrorCase.asyncSetUp(self)
        self.mirror.scheduler.cancel()
        await asyncio.gather(self.mirror.scheduler, return_exceptions=True)
        self.states, self.uploads, self.failed = {}, 0, False

        async def service(request):
            self.assertEqual(request.headers["Authorization"], "Bearer " + "t" * 40)
            if self.failed:
                return web.json_response({"error": "temporarily_unavailable"}, status=503)
            digest = request.match_info["digest"]
            if request.method == "PUT":
                payload = await request.read()
                if request.query["kind"] != "other":
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
                self.uploads += 1
                self.states[digest] = dict(
                    sha256=digest,
                    state="pending",
                    kind=request.query["kind"],
                    published=request.query["visibility"] == "public",
                    parser_version="1.0.0",
                )
            return web.json_response(
                self.states.get(
                    digest, dict(sha256=digest, state="missing", parser_version="1.0.0")
                )
            )

        app = web.Application()
        app.router.add_route("*", "/api/v1/internal/cached-artifacts/{digest}", service)
        self.service = TestServer(app)
        await self.service.start_server()
        self.config.facts_url = str(self.service.make_url("")).rstrip("/")
        self.config.facts_token = "t" * 40
        self.mirror.facts = CacheFacts(self.mirror)

    async def asyncTearDown(self):
        await mirror_fixture.MirrorCase.asyncTearDown(self)
        await self.service.close()

    async def test_local_cache_commit_notifies_and_failure_replays_after_restart(self):
        item = await self.mirror.download("https://libraries.minecraft.net/test/mod.jar")
        digest = item["object"]
        facts = self.mirror.facts
        row = await self.mirror.one("SELECT * FROM cache_facts WHERE digest=?", (digest,))
        self.assertEqual(row["kind"], "jar")
        self.assertTrue(await facts.needs_local("mirror", digest))
        self.failed = True
        await facts.run_once()
        row = await self.mirror.one("SELECT * FROM cache_facts WHERE digest=?", (digest,))
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["accepted"], 0)
        # New notifier owner reads the same persisted event, no lost memory callback.
        self.mirror.facts = facts = CacheFacts(self.mirror)
        await facts.open()
        await self.mirror.db.execute("UPDATE cache_facts SET next_at=0")
        await self.mirror.db.commit()
        self.failed = False
        await facts.run_once()
        self.assertEqual(self.uploads, 1)
        self.assertFalse(await facts.needs_local("mirror", digest))
        self.states[digest]["state"] = "complete_declared"
        await self.mirror.db.execute("UPDATE cache_facts SET next_at=0")
        await self.mirror.db.commit()
        await facts.run_once()
        row = await self.mirror.one("SELECT * FROM cache_facts WHERE digest=?", (digest,))
        self.assertEqual(row["state"], "complete_declared")
        self.assertEqual(self.uploads, 1)

    async def test_inventory_gap_reconciles_and_private_hit_is_explicitly_published(self):
        item = await self.mirror.download("https://libraries.minecraft.net/test/another.jar")
        digest = item["object"]
        await self.mirror.db.execute("DELETE FROM cache_facts WHERE digest=?", (digest,))
        await self.mirror.db.commit()
        self.states[digest] = dict(
            sha256=digest,
            state="complete_declared",
            kind="jar",
            published=False,
            parser_version="1.0.0",
        )
        await self.mirror.facts.run_once()
        self.assertEqual(self.uploads, 1)
        self.assertTrue(self.states[digest]["published"])
        self.assertEqual((await self.mirror.facts.status())["states"], {"pending": 1})

    async def test_nonarchives_are_local_not_applicable_and_unknown_archives_still_scan(self):
        facts = self.mirror.facts
        await facts.remember(
            "mirror", "a" * 64, 12, "https://example.test/icon.png", header=b"PNG!"
        )
        await facts.run_once()
        self.assertEqual(self.uploads, 0)
        self.assertEqual((await facts.status())["states"], {"not_applicable": 1})
        await facts.remember("mirror", "b" * 64, 34, "https://example.test/pack.mrpack")
        row = await self.mirror.one("SELECT * FROM cache_facts WHERE digest=?", ("b" * 64,))
        self.assertEqual(row["kind"], "archive")
        self.assertEqual(row["state"], "pending")
        await facts.remember(
            "mirror", "c" * 64, 50, "https://example.test/download/opaque", header=b"PK\x03\x04"
        )
        row = await self.mirror.one("SELECT * FROM cache_facts WHERE digest=?", ("c" * 64,))
        self.assertEqual(row["kind"], "archive")
        self.assertEqual(row["state"], "pending")
