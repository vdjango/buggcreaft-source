import hashlib
from dataclasses import replace

from conftest import fabric, jar_bytes
from fastapi.testclient import TestClient
from test_cached_facts import TOKEN

from bugg_source.api import create_app
from bugg_source.contracts import CollectionPaused, ReleaseIntake
from bugg_source.worker import Worker


def test_pause_survives_restart_and_releases_without_spending_attempts(settings, repository):
    from bugg_source.contracts import CollectRequest
    from bugg_source.repository import Repository

    identifier = repository.enqueue(CollectRequest(url="https://cdn.modrinth.com/file.jar"))
    assert repository.collection_control(True)["paused"]
    fresh = Repository(repository.engine)
    assert fresh.claim() is None
    fresh.collection_control(False)
    claimed, token, _ = fresh.claim()
    assert claimed == identifier
    fresh.collection_control(True)
    try:
        fresh.pulse(identifier, token)
    except CollectionPaused:
        fresh.finish(identifier, token, release=True)
    else:
        raise AssertionError("active worker ignored pause")
    assert fresh.get_job(identifier)["attempts"] == 0
    fresh.collection_control(False)
    assert fresh.claim()[0] == identifier


def test_requested_release_uses_original_worker_and_public_hash_facts(settings, repository):
    settings = replace(settings, cache_ingest_token=TOKEN)
    payload = jar_bytes({"fabric.mod.json": fabric(depends={"library": ">=2 <3"})})
    digest = hashlib.sha256(payload).hexdigest()
    store = Worker(settings, repository).collector.store
    # Reuse a real local CAS object, so intake doesn't need an external network.
    local = settings.data_dir / "incoming.jar"
    local.write_bytes(payload)
    store.import_local(local, lambda: None)
    observation = ReleaseIntake(
        provider="modrinth",
        project_id="project",
        release_id="release",
        url="https://cdn.modrinth.com/file.jar",
        digest=digest,
        algorithm="sha256",
        size=len(payload),
        origin="request",
        publisher_data={"id": "release", "project_id": "project", "name": "Observed version"},
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/dependency-collection/control").status_code == 401
        auth = {"Authorization": "Bearer test-token"}
        assert client.post(
            "/api/v1/dependency-collection/control", json={"paused": True}, headers=auth
        ).json()["paused"]
        assert client.get("/api/v1/dependency-collection/control", headers=auth).json()["paused"]
        client.post("/api/v1/dependency-collection/control", json={"paused": False}, headers=auth)
        headers = {"Authorization": "Bearer " + TOKEN}
        result = client.post(
            "/api/v1/internal/dependency-releases", json=observation.model_dump(), headers=headers
        )
        assert result.status_code == 200
        identifier = result.json()["job"]["id"]
        assert Worker(settings, repository).run_once()
        again = client.post(
            "/api/v1/internal/dependency-releases", json=observation.model_dump(), headers=headers
        ).json()
        assert again["job"]["id"] == identifier
        assert again["facts"]["mods"][0]["dependencies"][0]["version_raw"] == ">=2 <3"
        public = client.post("/api/v1/dependency-facts/query", json={"sha256": [digest]}).json()
        assert public["items"][0]["state"] == "complete_declared"
        identity = {"provider": "modrinth", "project_id": "project", "release_id": "release"}
        query = {"releases": [identity]}
        releases = client.post("/api/v1/dependency-releases/query", json=query).json()
        assert releases["items"][0]["state"] == "observed"
        snapshot = releases["items"][0]["snapshots"][0]
        assert snapshot["verified_sha256"] == digest
        assert snapshot["observation"]["publisher_data"]["name"] == "Observed version"
        assert "url" not in snapshot["observation"]
        assert client.post("/api/v1/dependency-releases/query", json=query).json() == releases

    # Reopening the service does not depend on another upstream request or the cache process.
    with TestClient(create_app(settings)) as client:
        result = client.post("/api/v1/dependency-releases/query", json=query).json()
        assert result["items"][0]["snapshots"][0]["revision"] == snapshot["revision"]
        missing = client.post(
            "/api/v1/dependency-releases/query",
            json={"releases": [dict(identity, release_id="unknown")]},
        ).json()
        assert missing["items"][0]["state"] == "missing"
        assert (
            client.post("/api/v1/dependency-releases/query", json={"releases": []}).status_code
            == 422
        )


def test_explicit_retry_updates_release_observation_to_original_queue_replacement(
    settings, repository
):
    observation = ReleaseIntake(
        provider="modrinth",
        project_id="project",
        release_id="release",
        url="https://cdn.modrinth.com/mod.jar",
        digest="a" * 64,
        algorithm="sha256",
        size=100,
    )
    first = repository.observe_release(observation)["job"]["id"]
    identifier, token, _ = repository.claim()
    assert first == identifier
    repository.finish(identifier, token, error="download_deadline")
    replacement = repository.retry(identifier)
    assert replacement and replacement != identifier
    assert repository.observe_release(observation)["job"]["id"] == replacement
