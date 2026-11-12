from datetime import timedelta

import pytest
from conftest import fabric, fake_collector, jar_bytes
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from bugg_source.api import create_app
from bugg_source.contracts import (
    PARSER_VERSION,
    Cancelled,
    CollectionError,
    CollectRequest,
    ScanResult,
)
from bugg_source.database import Artifact, Job, Scan
from bugg_source.repository import Repository, utcnow
from bugg_source.worker import Worker

URL = "https://cdn.modrinth.com/example.jar"


def test_worker_persists_once_and_query_survives_repository_restart(settings, repository):
    collector = fake_collector(
        settings, jar_bytes({"fabric.mod.json": fabric(depends={"library": "^1"})})
    )
    identifiers = [repository.enqueue(CollectRequest(url=URL)) for _ in range(2)]
    worker = Worker(settings, repository, collector)
    assert identifiers[0] == identifiers[1]
    assert worker.run_once()
    assert not worker.run_once()
    restarted = Repository(repository.engine)
    digest = restarted.get_job(identifiers[0])["artifact_sha256"]
    assert (
        restarted.get_scan(digest, PARSER_VERSION)["mods"][0]["dependencies"][0]["target_id"]
        == "library"
    )
    with Session(repository.engine) as session:
        assert len(list(session.scalars(select(Artifact)))) == 1
        assert len(list(session.scalars(select(Scan)))) == 1


def test_expired_lease_recovery_and_stale_worker_cannot_commit(repository):
    identifier = repository.enqueue(CollectRequest(url=URL))
    _, old_token, _ = repository.claim()
    with Session(repository.engine) as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == identifier)
            .values(lease_until=utcnow() - timedelta(seconds=1))
        )
    _, new_token, _ = repository.claim()
    assert old_token != new_token
    assert not repository.finish(identifier, old_token, error="stale")
    with pytest.raises(Cancelled):
        repository.pulse(identifier, old_token)
    repository.finish(identifier, new_token, error="expected_failure")
    assert repository.get_job(identifier)["attempts"] == 2


def test_cancel_running_then_retry_creates_new_task(repository):
    identifier = repository.enqueue(CollectRequest(url=URL))
    _, token, _ = repository.claim()
    repository.cancel(identifier)
    with pytest.raises(Cancelled):
        repository.pulse(identifier, token)
    repository.finish(identifier, token, error="cancelled")
    assert repository.get_job(identifier)["state"] == "cancelled"
    retry_id = repository.retry(identifier)
    assert retry_id != identifier
    assert repository.get_job(retry_id)["state"] == "queued"


def test_worker_graceful_stop_requeues_task(settings, repository):
    identifier = repository.enqueue(CollectRequest(url=URL))
    worker = Worker(settings, repository, fake_collector(settings, b"unused"))
    worker.stop()
    worker.run_once()
    assert repository.get_job(identifier)["state"] == "queued"


def test_api_requires_auth_enqueues_only_and_returns_persisted_result(settings, repository):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/collections").status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        response = client.post("/api/v1/collections", headers=headers, json={"url": URL})
        assert response.status_code == 202
        identifier = response.json()["id"]
        assert response.json()["state"] == "queued"
        assert list((settings.data_dir / "blobs").glob("*.jar")) == []
        assert (
            client.post(
                "/api/v1/collections", headers=headers, json={"url": "http://localhost/x"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/collections",
                headers=headers,
                json={"url": URL, "description": "invented dependency"},
            ).status_code
            == 422
        )
        Worker(
            settings, repository, fake_collector(settings, jar_bytes({"fabric.mod.json": fabric()}))
        ).run_once()
        job = client.get(f"/api/v1/collections/{identifier}", headers=headers).json()
        result = client.get(
            f"/api/v1/artifacts/{job['artifact_sha256']}/dependencies", headers=headers
        )
        assert result.json()["mods"][0]["declared_name"] == "包内真实名"
        assert "url" not in job
        assert job["verification"] == "unverified"


def test_private_api_success_and_errors_cannot_be_shared_cached(settings, repository):
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer test-token"}
        authenticated = client.get("/api/v1/artifacts", headers=headers)
        anonymous = client.get("/api/v1/artifacts")
        invalid = client.get("/api/v1/artifacts?limit=0", headers=headers)
        missing = client.get("/api/v1/missing")
        assert [r.status_code for r in (authenticated, anonymous, invalid, missing)] == [
            200,
            401,
            422,
            404,
        ]
        for response in (authenticated, anonymous, invalid, missing):
            assert response.headers["Cache-Control"] == "no-store"
        assert "items" not in anonymous.json()


def test_lease_retry_limit_becomes_failed_without_hiding_task(repository):
    identifier = repository.enqueue(CollectRequest(url=URL))
    for _ in range(3):
        assert repository.claim()[0] == identifier
        with Session(repository.engine) as session, session.begin():
            session.execute(
                update(Job)
                .where(Job.id == identifier)
                .values(lease_until=utcnow() - timedelta(seconds=1))
            )
    assert repository.claim() is None
    assert repository.get_job(identifier)["state"] == "failed"
    assert repository.get_job(identifier)["error"] == "lease_retry_limit"


def test_artifact_directory_uses_stable_cursors_without_job_history(settings, repository):
    for digest, version in [("a" * 64, "1"), ("a" * 64, "2"), ("b" * 64, "1")]:
        repository.save_local_scan(
            ScanResult(
                artifact_sha256=digest,
                size=123,
                parser_version=version,
                status="unsupported",
            )
        )
    with Session(repository.engine) as session, session.begin():
        session.execute(update(Scan).values(created_at=utcnow().replace(microsecond=0)))
    first = repository.artifacts(2)
    # A newly inserted scan must not shift the second cursor page.
    repository.save_local_scan(
        ScanResult(
            artifact_sha256="c" * 64,
            size=456,
            status="unsupported",
        )
    )
    second = repository.artifacts(2, first["next_cursor"])
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert second["next_cursor"] is None
    items = first["items"] + second["items"]
    assert len({item["scan_id"] for item in items}) == 3
    assert all("result" not in item and "mods" not in item for item in items)
    assert repository.jobs() == []
    with pytest.raises(CollectionError, match="invalid_artifact_cursor"):
        repository.artifacts(50, "0" * 36)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/artifacts").status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        response = client.get("/api/v1/artifacts?limit=1", headers=headers)
        assert len(response.json()["items"]) == 1 and response.json()["next_cursor"]
        assert client.get("/api/v1/artifacts?limit=101", headers=headers).status_code == 422
        assert (
            client.get("/api/v1/artifacts?cursor=" + "0" * 36, headers=headers).status_code == 400
        )
