import asyncio
import hashlib
from dataclasses import replace

from conftest import fabric, jar_bytes
from fastapi.testclient import TestClient

from bugg_source.api import create_app
from bugg_source.contracts import PARSER_VERSION
from bugg_source.repository import Repository
from bugg_source.storage import ArtifactStore
from bugg_source.worker import Worker

TOKEN = "cache-service-test-token-32-characters"


def test_cancelled_ingest_never_publishes_partial_archive(settings):
    store = ArtifactStore(settings)

    async def chunks():
        yield b"first chunk"
        raise asyncio.CancelledError()

    async def cancel_upload():
        try:
            await store.receive(chunks(), "a" * 64, 100)
        except asyncio.CancelledError:
            return
        raise AssertionError("upload ignored cancellation")

    asyncio.run(cancel_upload())
    assert not list((store.root / "tmp").iterdir())
    assert not list((store.root / "blobs").iterdir())


def upload(client, payload, *, visibility="public", kind="jar", digest=None, size=None):
    digest = digest or hashlib.sha256(payload).hexdigest()
    response = client.put(
        f"/api/v1/internal/cached-artifacts/{digest}",
        params=dict(kind=kind, visibility=visibility, size=len(payload) if size is None else size),
        content=payload,
        headers={"Authorization": "Bearer " + TOKEN},
    )
    return digest, response


def query(client, digest):
    response = client.post("/api/v1/dependency-facts/query", json={"sha256": [digest]})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["schema"] == "bugg-dependency-facts-v1"
    return response.json()["items"][0]


def test_cached_upload_queues_original_worker_and_reuses_immutable_facts(settings, repository):
    settings = replace(settings, cache_ingest_token=TOKEN)
    payload = jar_bytes({"fabric.mod.json": fabric(depends={"library": ">=2 <3"})})
    with TestClient(create_app(settings)) as client:
        digest, response = upload(client, payload)
        assert response.status_code == 200
        assert response.json()["state"] == "pending"
        assert query(client, digest)["state"] == "pending"
        assert Worker(settings, repository).run_once()
        item = query(client, digest)
        assert item["state"] == "complete_declared"
        assert item["facts"]["artifact_sha256"] == digest
        assert item["facts"]["mods"][0]["dependencies"][0]["version_raw"] == ">=2 <3"
        original = Repository(repository.engine).get_scan(digest, PARSER_VERSION)
        assert upload(client, payload)[1].json()["state"] == "complete_declared"
        assert not Worker(settings, repository).run_once()
        assert original == Repository(repository.engine).get_scan(digest, PARSER_VERSION)
        # Parser upgrades/replay can reuse the verified collector CAS without another transfer.
        reuse = client.put(
            f"/api/v1/internal/cached-artifacts/{digest}",
            params=dict(kind="jar", visibility="public", size=len(payload), reuse=True),
            headers={"Authorization": "Bearer " + TOKEN},
        )
        assert reuse.status_code == 200
        assert reuse.json()["state"] == "complete_declared"


def test_private_import_never_leaks_through_public_hash_query(settings, repository):
    settings = replace(settings, cache_ingest_token=TOKEN)
    payload = jar_bytes({"fabric.mod.json": fabric(name="private room file")})
    with TestClient(create_app(settings)) as client:
        digest, response = upload(client, payload, visibility="private")
        assert response.status_code == 200
        assert Worker(settings, repository).run_once()
        assert query(client, digest)["state"] == "missing"
        assert "size" not in query(client, digest)
        assert repository.get_scan(digest, PARSER_VERSION) is not None
        # An explicit public cache publication of these same verified bytes may expose facts.
        assert upload(client, payload)[1].status_code == 200
        assert query(client, digest)["state"] == "complete_declared"


def test_ingest_auth_hash_validation_nonjar_and_bounded_queries(settings, repository):
    settings = replace(settings, cache_ingest_token=TOKEN)
    with TestClient(create_app(settings)) as client:
        assert client.put("/api/v1/internal/cached-artifacts/" + "a" * 64).status_code == 403
        digest, response = upload(client, b"invalid jar", digest="a" * 64)
        assert response.status_code == 422
        assert query(client, digest)["state"] == "missing"
        assert not Worker(settings, repository).run_once()
        digest, response = upload(client, b"", kind="other", digest="b" * 64, size=5 * 1024**3)
        assert response.status_code == 200
        assert query(client, digest)["state"] == "not_applicable"
        assert "facts" not in query(client, digest)
        bad = client.post("/api/v1/dependency-facts/query", json={"sha256": ["x"]})
        assert bad.status_code == 422
        assert (
            client.post("/api/v1/dependency-facts/query", content=b" " * 16385).status_code == 413
        )
        assert (
            client.post(
                "/api/v1/dependency-facts/query", json={"sha256": ["c" * 64] * 101}
            ).status_code
            == 422
        )
        # Public collection admission still rejects internal CAS URLs.
        assert (
            client.post(
                "/api/v1/collections",
                json={"url": "cache://sha256/" + digest},
                headers={"Authorization": "Bearer test-token"},
            ).status_code
            == 422
        )


def test_invalid_archive_is_failed_and_different_release_facts_do_not_overwrite(
    settings, repository
):
    settings = replace(settings, cache_ingest_token=TOKEN)
    with TestClient(create_app(settings)) as client:
        first = jar_bytes({"fabric.mod.json": fabric(version="1", depends={"a": "<2"})})
        second = jar_bytes({"fabric.mod.json": fabric(version="2", depends={"a": ">=2", "c": "*"})})
        digests = [upload(client, payload)[0] for payload in (first, second)]
        worker = Worker(settings, repository)
        assert worker.run_once() and worker.run_once()
        assert (
            query(client, digests[0])["facts"]["mods"][0]["dependencies"][0]["version_raw"] == "<2"
        )
        assert len(query(client, digests[1])["facts"]["mods"][0]["dependencies"]) == 2
        unknown = jar_bytes({"unknown-package-format.json": "{}"})
        archive, response = upload(client, unknown, kind="archive")
        assert response.status_code == 200
        assert worker.run_once()
        assert query(client, archive)["state"] == "unsupported"
        broken, response = upload(client, b"not a ZIP")
        assert response.status_code == 200
        assert worker.run_once()
        assert query(client, broken)["state"] == "failed"
        assert "facts" not in query(client, broken)
