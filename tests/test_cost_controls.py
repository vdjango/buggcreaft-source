import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from conftest import fabric, fake_collector, jar_bytes
from fastapi.testclient import TestClient
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from bugg_source.api import create_app
from bugg_source.contracts import Cancelled, CollectionError, CollectRequest, QueueFull
from bugg_source.database import Job, Scan
from bugg_source.repository import Repository, utcnow
from bugg_source.worker import Worker

URL = "https://cdn.modrinth.com/test.jar"


def test_unhashed_url_can_change_contents(settings, repository):
    repo = Repository(repository.engine, settings=settings)
    first = repo.enqueue(CollectRequest(url=URL))
    Worker(
        settings,
        repo,
        fake_collector(settings, jar_bytes({"fabric.mod.json": fabric(name="first")})),
    ).run_once()
    second = repo.enqueue(CollectRequest(url=URL))
    Worker(
        settings,
        repo,
        fake_collector(settings, jar_bytes({"fabric.mod.json": fabric(name="second")})),
    ).run_once()
    assert repo.get_job(first)["artifact_sha256"] != repo.get_job(second)["artifact_sha256"]


def test_sha1_is_scoped_to_url_and_settings_limits(settings, repository, monkeypatch):
    first = repository.enqueue(
        CollectRequest(url=URL, hash_algorithm="sha1", expected_hash="a" * 40)
    )
    second = repository.enqueue(
        CollectRequest(url=URL + "?different=1", hash_algorithm="sha1", expected_hash="a" * 40)
    )
    assert first != second
    monkeypatch.setenv("BUGG_MAX_PENDING_JOBS", "7")
    assert type(settings).from_env().max_pending_jobs == 7
    assert replace(settings, max_depth=0).max_depth == 0
    with pytest.raises(ValueError, match="invalid_limit"):
        replace(settings, max_pending_jobs=0)


def test_manifest_warmup_and_batch_boundary(settings, repository, tmp_path, monkeypatch, capsys):
    import json
    import sys

    from bugg_source.cli import main

    manifest = tmp_path / "files.jsonl"
    manifest.write_text(
        "\n".join(json.dumps({"url": URL + f"?n={n}"}) for n in range(2)), encoding="utf-8"
    )
    monkeypatch.setenv("BUGG_DATA_DIR", str(settings.data_dir))
    monkeypatch.setattr(
        sys, "argv", ["bugg-source", "enqueue-manifest", str(manifest), "--limit", "1", "--warmup"]
    )
    with pytest.raises(SystemExit, match="manifest_batch_limit_exceeded"):
        main()
    assert len(repository.jobs()) == 1 and repository.jobs()[0]["priority"] == 10
    assert repository.jobs()[0]["id"] in capsys.readouterr().out


def test_parallel_admission_merges_and_promotes(settings, repository):
    repo = Repository(repository.engine, settings=settings)
    request = CollectRequest(url=URL, purpose="warmup")
    with ThreadPoolExecutor(max_workers=6) as pool:
        identifiers = list(pool.map(lambda _: repo.enqueue(request), range(12)))
    assert len(set(identifiers)) == 1
    assert repo.get_job(identifiers[0])["priority"] == 10
    assert repo.enqueue(CollectRequest(url=URL)) == identifiers[0]
    assert repo.get_job(identifiers[0])["priority"] == 100


def test_queue_cap_duplicate_and_priority(settings, repository):
    repo = Repository(repository.engine, settings=replace(settings, max_pending_jobs=2))
    warm = repo.enqueue(CollectRequest(url=URL, purpose="warmup"))
    hot = repo.enqueue(CollectRequest(url=URL + "?hot=1"))
    assert repo.enqueue(CollectRequest(url=URL + "?hot=1")) == hot
    with pytest.raises(QueueFull):
        repo.enqueue(CollectRequest(url=URL + "?another=1"))
    assert repo.claim()[0] == hot
    assert repo.claim()[0] == warm


def test_worker_global_running_limit_and_expired_token(settings, repository):
    repo = Repository(repository.engine, settings=replace(settings, max_running_jobs=1))
    first = repo.enqueue(CollectRequest(url=URL))
    repo.enqueue(CollectRequest(url=URL + "?second=1"))
    identifier, token, _ = repo.claim()
    assert identifier == first and repo.claim() is None
    with Session(repo.engine) as session, session.begin():
        session.execute(
            update(Job).where(Job.id == first).values(lease_until=utcnow() - timedelta(seconds=1))
        )
    new_id, new_token, _ = repo.claim()
    assert new_id == first and token != new_token
    with pytest.raises(Cancelled):
        repo.reserve_download(first, token)


@pytest.mark.parametrize("algorithm", ["sha256", "sha512"])
def test_strong_hash_cache_cross_url_and_no_network(settings, repository, algorithm):
    repo = Repository(repository.engine, settings=settings)
    payload = jar_bytes({"fabric.mod.json": fabric()})
    digest = hashlib.new(algorithm, payload).hexdigest()
    request = CollectRequest(url=URL, hash_algorithm=algorithm, expected_hash=digest)
    first = repo.enqueue(request)
    collector = fake_collector(settings, payload)
    worker = Worker(settings, repo, collector)
    assert worker.run_once()
    collector.collect = Mock(side_effect=AssertionError("cached request must not collect"))
    assert repo.enqueue(request.model_copy(update={"url": URL + "?different=1"})) == first
    assert not worker.run_once()
    assert repo.get_job(first)["verification"] == "expected_hash_matched"
    # Cross-algorithm identity can reuse facts through the SHA-256 CAS key too.
    other = repo.enqueue(CollectRequest(url=URL, expected_hash=hashlib.sha256(payload).hexdigest()))
    assert repo.get_job(other)["state"] == "succeeded"


def test_unhashed_or_sha1_download_again_but_skip_duplicate_parse(settings, repository):
    repo = Repository(repository.engine, settings=settings)
    payload = jar_bytes({"fabric.mod.json": fabric()})
    collector = fake_collector(settings, payload)
    collector._visit = Mock(wraps=collector._visit)
    worker = Worker(settings, repo, collector)
    for algorithm, expected in [("sha256", None), ("sha1", hashlib.sha1(payload).hexdigest())]:
        request = CollectRequest(url=URL, hash_algorithm=algorithm, expected_hash=expected)
        first = repo.enqueue(request)
        assert worker.run_once()
        second = repo.enqueue(request)
        assert first != second
        assert worker.run_once()
    assert collector._visit.call_count == 1
    assert repo.get_job(second)["cache_hit"]


def test_known_local_blob_reparse_without_network_or_budget(settings, repository):
    repo = Repository(repository.engine, settings=settings)
    payload = jar_bytes({"fabric.mod.json": fabric()})
    request = CollectRequest(url=URL, expected_hash=hashlib.sha256(payload).hexdigest())
    repo.enqueue(request)
    collector = fake_collector(settings, payload)
    Worker(settings, repo, collector).run_once()
    spent = repo.status()["reserved_download_bytes"]
    with Session(repo.engine) as session, session.begin():
        session.execute(delete(Scan))
    collector.store.download = Mock(side_effect=AssertionError("local blob exists"))
    identifier = repo.enqueue(request)
    Worker(settings, repo, collector).run_once()
    assert repo.get_job(identifier)["state"] == "succeeded"
    assert repo.status()["reserved_download_bytes"] == spent
    newer = Repository(repo.engine, settings=settings, parser_version="next-test")
    assert newer.get_job(newer.enqueue(request))["state"] == "queued"


def test_budget_atomic_idempotent_no_refund(settings, repository):
    limits = replace(settings, max_download_bytes=100, daily_download_bytes=100)
    repo = Repository(repository.engine, settings=limits)
    for n in range(2):
        repo.enqueue(CollectRequest(url=URL + f"?n={n}"))
    first, second = repo.claim(), repo.claim()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: repo.reserve_download(first[0], first[1]), range(4)))
    assert repo.status()["reserved_download_bytes"] == 100
    repo.finish(first[0], first[1], error="failed")
    with pytest.raises(CollectionError, match="daily_download_budget_exhausted"):
        repo.reserve_download(second[0], second[1])


def test_disk_floor_prevents_download(settings, monkeypatch):
    collector = fake_collector(settings, b"should not be downloaded")
    monkeypatch.setattr("bugg_source.storage.shutil.disk_usage", lambda _: Mock(free=0))
    with pytest.raises(CollectionError, match="disk_free_space_low"):
        collector.collect(CollectRequest(url=URL), lambda: None)
    assert not list((settings.data_dir / "tmp").iterdir())


def test_api_cache_200_queue_429_and_status_auth(settings, repository):
    limits = replace(settings, max_pending_jobs=1)
    repo = Repository(repository.engine, settings=limits)
    payload = jar_bytes({"fabric.mod.json": fabric()})
    request = CollectRequest(url=URL, expected_hash=hashlib.sha256(payload).hexdigest())
    repo.enqueue(request)
    Worker(limits, repo, fake_collector(limits, payload)).run_once()
    with TestClient(create_app(limits)) as client:
        headers = {"Authorization": "Bearer test-token"}
        assert (
            client.post(
                "/api/v1/collections", json=request.model_dump(), headers=headers
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/collections", json={"url": URL + "?queued=1"}, headers=headers
            ).status_code
            == 202
        )
        full = client.post("/api/v1/collections", json={"url": URL + "?other=1"}, headers=headers)
        assert full.status_code == 429 and full.headers["Retry-After"] == "30"
        assert (
            client.post(
                "/api/v1/collections", json=request.model_dump(), headers=headers
            ).status_code
            == 200
        )
        assert client.get("/api/v1/storage/status").status_code == 401
        status = client.get("/api/v1/storage/status", headers=headers).json()
        assert status["active"]["queued"] == 1
        assert status["cloud"] == {"configured": False}
        assert "test-token" not in str(status)
