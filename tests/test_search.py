"""Real PostgreSQL and HTTP hub; synthetic vectors are explicitly test doubles."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from aiohttp.test_utils import TestServer
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import text

from bugg_source.api import create_app
from bugg_source.config import Settings
from bugg_source.database import make_engine
from bugg_source.search.clients import SearchClients, SearchUnavailable
from bugg_source.search.config import SearchSettings
from bugg_source.search.repository import SearchRepository
from bugg_source.search.service import SearchService
from bugg_source.search.text import chunks, clean, snippet, terms
from bugg_source.search.worker import SearchWorker
from mc_translate.api import create_app as create_hub
from mc_translate.config import Config
from mc_translate.hashing import digest, resource_ref, text_hash
from mc_translate.store import POLICY


def project(
    identifier="Storage01", body="使用铜制漏斗对箱子中的物品进行自动分类。", *, releases=None
):
    fields = {}
    for name, value in [
        ("title", "Workshop " + identifier),
        ("summary", "An adventure utility."),
        ("body", body),
    ]:
        fields[name] = dict(
            text=value,
            format="markdown",
            source_language="zh-Hans",
            source_hash=text_hash(value, name, "markdown"),
        )
    for release in releases or []:
        release.setdefault("dependencies", [])
    return dict(
        resource=resource_ref("modrinth", identifier),
        kind="mod",
        source_url="https://modrinth.com/project/" + identifier,
        fields=fields,
        snapshot_hash=digest(fields),
        metadata_hash=digest(releases or []),
        metadata=dict(slug=identifier.lower(), releases=releases or []),
    )


class HubThread:
    def __init__(self, directory):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.call(self.start(directory))

    async def start(self, directory):
        self.app = await create_hub(
            Config(
                database=str(directory / "hub.sqlite3"),
                mirror_root=str(directory / "mirror"),
                admin_token="search-admin",
            ),
            workers=False,
        )
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")
        self.store = self.app["store"]

    def call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=30)

    def close(self):
        self.call(self.server.close())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("BUGG_PLATFORM_AUTH_TOKEN", "search-mesh-test")
    server = HubThread(tmp_path)
    yield server
    server.close()


@pytest.fixture
def search_repo(hub, monkeypatch):
    url = os.getenv("BUGG_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("BUGG_TEST_POSTGRES_URL is required for real search tests")
    monkeypatch.setenv("BUGG_DATABASE_URL", url)
    command.upgrade(AlembicConfig("alembic.ini"), "head")
    engine = make_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                """
                TRUNCATE
                search_projects,search_jobs,search_chunks,search_sessions,search_rate_limits
                RESTART IDENTITY CASCADE
                """
            )
        )
        db.execute(
            text(
                """
                UPDATE search_control SET
                epoch='',cursor=0,watermark=0,feed_until=NULL,feed_token=NULL
                WHERE id=1
                """
            )
        )
    settings = SearchSettings(enabled=True, source_url=hub.url, source_token="search-mesh-test")
    repository = SearchRepository(engine, settings)
    yield repository
    engine.dispose()


def index_all(repository):
    clients = SearchClients(repository.settings)
    worker = SearchWorker(repository, clients)
    try:
        for _ in range(30):
            if not worker.run_once():
                break
    finally:
        clients.close()


def test_text_body_cleaning_short_terms_and_unicode():
    assert "自动分类" in terms("自动分类")
    assert "fabric-api" in terms("Fabric-API")
    assert "隐藏" not in clean("<script>隐藏</script><p>铜制漏斗</p>", "html")
    assert "网站" in clean("[网站](https://example.com)", "markdown")
    assert "https://" not in clean("[网站](https://example.com)", "markdown")
    value = snippet("😀这里有铜制漏斗", "铜制")
    assert value["highlights"][0]["start"] == 4
    parts, coverage = chunks(
        project(body="正文独有词")
        | {
            "documents": [
                dict(
                    field="body",
                    language="zh-Hans",
                    text="正文独有词",
                    format="plain",
                    source_hash="hash",
                    revision="r",
                    is_translation=False,
                )
            ]
        }
    )
    assert coverage["body_available"] and parts[0]["text"] == "正文独有词"


def test_internal_feed_is_durable_current_and_withdrawal_changes_revision(hub):
    hub.call(hub.store.save_source(project()))
    with httpx.Client(base_url=hub.url, trust_env=False) as client:
        assert client.get("/_internal/search/changes").status_code == 401
        headers = {"X-Platform-Auth": "search-mesh-test"}
        first = client.get("/_internal/search/changes", headers=headers).json()
        assert first["items"][0]["documents"][2]["field"] == "body"
        assert "铜制漏斗" in first["items"][0]["documents"][2]["text"]
        assert (
            client.post(
                "/_internal/search/visibility",
                headers=headers,
                json={"project_id": "modrinth:Storage01", "visible": False},
            ).status_code
            == 200
        )
        second = client.get(
            "/_internal/search/changes", params={"after": first["cursor"]}, headers=headers
        ).json()
        assert not second["items"][0]["visible"]
        assert second["items"][0]["revision"] > first["items"][0]["revision"]


@pytest.mark.postgres
def test_postgres_body_filters_pagination_api_permissions_and_immediate_hide(
    search_repo, hub, tmp_path
):
    for identifier in ("Storage01", "Storage02", "Storage03"):
        hub.call(
            hub.store.save_source(
                project(
                    identifier,
                    releases=[
                        dict(id="a", game_versions=["1.21"], loaders=["forge"]),
                        dict(id="b", game_versions=["1.20"], loaders=["fabric"]),
                    ],
                )
            )
        )
    index_all(search_repo)
    settings = Settings(
        database_url=str(search_repo.engine.url.render_as_string(hide_password=False)),
        data_dir=tmp_path / "api",
        admin_token="api-admin",
    )
    app = create_app(settings, search_repo.settings)
    # Distinct API instances share cursor state, no process affinity.
    second = create_app(settings, search_repo.settings)
    with TestClient(app) as client, TestClient(second) as other:
        result = client.get(
            "/api/v1/search", params={"q": "铜制漏斗", "mode": "lexical", "limit": 1}
        )
        assert result.status_code == 200, result.text
        value = result.json()
        assert len(value["items"]) == 1
        assert value["items"][0]["snippets"][0]["field"] == "body"
        assert (
            client.get(
                "/api/v1/search", params={"q": "铜制漏斗火箭发射", "mode": "lexical"}
            ).json()["items"]
            == []
        )
        cursor = value["next_cursor"]
        assert cursor
        assert (
            other.get(
                "/api/v1/search",
                params={"q": "铜制漏斗", "mode": "lexical", "limit": 1, "cursor": cursor},
            ).status_code
            == 200
        )
        assert (
            other.get(
                "/api/v1/search", params={"q": "其他", "mode": "lexical", "cursor": cursor}
            ).status_code
            == 410
        )
        assert (
            client.get(
                "/api/v1/search",
                params={
                    "q": "铜制漏斗",
                    "mode": "lexical",
                    "mc_version": "1.21",
                    "loader": "fabric",
                },
            ).json()["items"]
            == []
        )
        assert client.get("/api/v1/admin/search/status").status_code == 401
        assert (
            client.post(
                "/api/v1/admin/search/rebuilds",
                json={},
                headers={"Authorization": "Bearer api-admin"},
            ).status_code
            == 202
        )
        # An older search session must not reveal a withdrawn project, even before worker ingestion.
        with httpx.Client(
            base_url=hub.url, trust_env=False, headers={"X-Platform-Auth": "search-mesh-test"}
        ) as authority:
            for identifier in ("Storage01", "Storage02", "Storage03"):
                authority.post(
                    "/_internal/search/visibility",
                    json={"project_id": "modrinth:" + identifier, "visible": False},
                ).raise_for_status()
        assert (
            client.get("/api/v1/search", params={"q": "铜制漏斗", "mode": "lexical"}).json()[
                "items"
            ]
            == []
        )


@pytest.mark.postgres
def test_distributed_claims_expiry_fencing_and_feed_epoch(search_repo, hub):
    for number in range(8):
        hub.call(hub.store.save_source(project("Test" + str(number))))
    first = search_repo.feed_claim()
    assert first and search_repo.feed_claim() is None
    search_repo.feed_release(first[0])
    clients = SearchClients(search_repo.settings)
    try:
        SearchWorker(search_repo, clients).ingest()
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: search_repo.claim(), range(8)))
        assert all(claims) and len({c["project_id"] for c in claims}) == 8
        job = claims[0]
        with search_repo.engine.begin() as db:
            db.execute(
                text(
                    """
                UPDATE search_jobs SET lease_until=now()-interval '1 second'
                WHERE project_id=:id
                """
                ),
                {"id": job["project_id"]},
            )
        newer = search_repo.claim()
        assert newer["claim_token"] != job["claim_token"]
        assert not search_repo.publish(job, [], {})
        assert search_repo.publish(newer, [], {})
        with search_repo.engine.begin() as db:
            db.execute(
                text("""
                UPDATE search_control SET epoch='wrong-epoch'
                """)
            )
        with pytest.raises(SearchUnavailable, match="epoch_changed"):
            clients.validate([job["project_id"]], epoch=search_repo.epoch())
    finally:
        clients.close()


@pytest.mark.postgres
def test_semantic_vectors_hybrid_degradation_and_revision_space(search_repo, hub):
    # Explicit unit vectors verify orchestration, not model quality; real model smoke is separate.
    hub.call(hub.store.save_source(project()))
    configuration = replace(
        search_repo.settings,
        embedding_url="http://127.0.0.1:1",
        embedding_token="test",
        model_revision="test-vector-space",
        dimensions=3,
    )
    repo = SearchRepository(search_repo.engine, configuration)
    clients = SearchClients(configuration)
    worker = SearchWorker(repo, clients)
    worker.ingest()
    job = repo.claim()
    pieces, coverage = chunks(job["payload"])
    for item in pieces:
        item["embedding"] = [1.0, 0.0, 0.0]
    assert repo.publish(job, pieces, coverage)
    service = SearchService(repo, clients)
    try:
        clients.embed = lambda texts, query=False: [[1.0, 0.0, 0.0] for _ in texts]
        result = service.search("完全不同的问法", "semantic", {"language": "zh-Hans"})
        assert result["items"][0]["project_id"] == "modrinth:Storage01"

        def unavailable(*args, **kwargs):
            raise SearchUnavailable("semantic_unavailable")

        clients.embed = unavailable
        result = service.search("铜制漏斗", "hybrid", {"language": "zh-Hans"})
        assert result["degraded"] and result["effective_mode"] == "lexical" and result["items"]
        with pytest.raises(SearchUnavailable):
            service.search("物品", "semantic", {"language": "zh-Hans"})
    finally:
        clients.close()


@pytest.mark.postgres
def test_current_body_translation_withdrawal_and_out_of_order_jobs(search_repo, hub):
    source = project(body="Original English paragraph without a Chinese keyword.")
    hub.call(hub.store.save_source(source))

    async def publish_translation():
        async with hub.store.transaction() as db:
            await db.execute(
                """
                INSERT INTO
                translations(key,field,source_hash,language,context,policy,
                revision,quality,text,artifact,generated,format)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "modrinth:Storage01",
                    "body",
                    source["fields"]["body"]["source_hash"],
                    "zh-Hans",
                    hub.store.context(source),
                    POLICY,
                    "body-tr-v1",
                    "machine",
                    "星辉机械可以自动分拣背包物资。",
                    "artifact",
                    1,
                    "markdown",
                ),
            )

    hub.call(publish_translation())
    index_all(search_repo)
    clients = SearchClients(search_repo.settings)
    service = SearchService(search_repo, clients)
    try:
        result = service.search("星辉机械", "lexical", {"language": "zh-Hans"})
        assert result["items"][0]["snippets"][0]["is_translation"]
        assert result["items"][0]["snippets"][0]["field"] == "body"

        async def withdraw():
            async with hub.store.transaction() as db:
                await db.execute("""
                DELETE FROM translations WHERE field='body'
                """)

        hub.call(withdraw())
        # No worker needs to run to hide the now-invalid translation.
        assert not service.search("星辉机械", "lexical", {"language": "zh-Hans"})["items"]
        worker = SearchWorker(search_repo, clients)
        worker.ingest()
        old = search_repo.claim()
        hub.call(hub.store.save_source(project(body="新版正文只有月辉合金。")))
        worker.ingest()
        assert not search_repo.publish(old, [], {})
        index_all(search_repo)
        assert service.search("月辉合金", "lexical", {"language": "zh-Hans"})["items"]
        assert not service.search("星辉机械", "lexical", {"language": "zh-Hans"})["items"]
    finally:
        clients.close()


@pytest.mark.postgres
def test_cursor_expiry_source_failure_and_model_contract(search_repo, hub):
    hub.call(hub.store.save_source(project()))
    hub.call(hub.store.save_source(project("Storage02")))
    index_all(search_repo)
    clients = SearchClients(search_repo.settings)
    service = SearchService(search_repo, clients)
    try:
        result = service.search("铜制漏斗", "lexical", {"language": "zh-Hans"}, limit=1)
        with search_repo.engine.begin() as db:
            db.execute(
                text("""
                UPDATE search_sessions SET expires_at=now()-interval '1
                second'
                """)
            )
        with pytest.raises(SearchUnavailable, match="expired"):
            service.search(
                "铜制漏斗", "lexical", {"language": "zh-Hans"}, cursor=result["next_cursor"]
            )
        broken = SearchClients(replace(search_repo.settings, source_url="http://127.0.0.1:1"))
        try:
            with pytest.raises(SearchUnavailable, match="authority_unavailable"):
                SearchService(search_repo, broken).search(
                    "铜制漏斗", "lexical", {"language": "zh-Hans"}
                )
        finally:
            broken.close()
        clients.call = lambda *args, **kwargs: dict(revision="wrong", vectors=[[0.0, 0.0, 0.0]])
        with pytest.raises(SearchUnavailable, match="space_mismatch"):
            clients.embed(["文本"])
    finally:
        clients.close()
