import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT

from bugg_source.config import Settings
from bugg_source.contracts import CollectionError, CollectRequest
from bugg_source.database import make_engine
from bugg_source.repository import Repository


@pytest.mark.postgres
def test_postgres_migration_and_concurrent_claims(monkeypatch):
    url = os.getenv("BUGG_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("BUGG_TEST_POSTGRES_URL is not configured")
    monkeypatch.setenv("BUGG_DATABASE_URL", url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = make_engine(url)
    repository = Repository(engine, settings=replace(Settings(), max_running_jobs=8))
    try:
        queued = {
            repository.enqueue(CollectRequest(url=f"https://cdn.modrinth.com/example-{n}.jar"))
            for n in range(8)
        }
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(claim_one, [repository] * 8))
        assert {claim[0] for claim in claims} == queued
        assert len({claim[1] for claim in claims}) == 8
        for identifier, token, _ in claims:
            repository.pulse(identifier, token)
            assert repository.finish(identifier, token, error="integration_test_completed")
        # The same database gate must protect admission across API processes too.
        request = CollectRequest(url="https://cdn.modrinth.com/shared-integration.jar")
        with ThreadPoolExecutor(max_workers=8) as pool:
            shared = list(pool.map(lambda _: repository.enqueue(request), range(16)))
        assert len(set(shared)) == 1
        shared_claim = repository.claim()
        repository.finish(shared_claim[0], shared_claim[1], error="integration_completed")
        limited = Repository(
            engine,
            settings=replace(
                Settings(), max_download_bytes=100, daily_download_bytes=200, max_running_jobs=8
            ),
        )
        for n in range(4):
            limited.enqueue(CollectRequest(url=f"https://cdn.modrinth.com/budget-{n}.jar"))
        budget_claims = [limited.claim() for _ in range(4)]

        def reserve(claim):
            try:
                limited.reserve_download(claim[0], claim[1])
                return True
            except CollectionError as exc:
                assert str(exc) == "daily_download_budget_exhausted"
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(reserve, budget_claims)) == 2
        assert limited.status()["reserved_download_bytes"] == 200
        for identifier, token, _ in budget_claims:
            limited.finish(identifier, token, error="integration_completed")
    finally:
        engine.dispose()


def claim_one(repository):
    return repository.claim()
