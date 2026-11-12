"""Reproducible isolated cache-admission timing; no external requests or QPS claim."""

import argparse
import hashlib
import io
import json
import statistics
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import Mock

import httpx

from bugg_source.collector import Collector
from bugg_source.config import Settings
from bugg_source.contracts import CollectRequest
from bugg_source.database import Base, make_engine
from bugg_source.repository import Repository
from bugg_source.storage import ArtifactStore
from bugg_source.worker import Worker


def benchmark(iterations: int) -> dict:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "id": "benchmark_mod",
                    "name": "Benchmark Mod",
                    "version": "1.0.0",
                    "depends": {"minecraft": ">=1.21"},
                }
            ),
        )
    payload = stream.getvalue()
    transfers = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal transfers
        transfers += 1
        return httpx.Response(200, stream=httpx.ByteStream(payload))

    with tempfile.TemporaryDirectory(prefix="bugg-cache-") as directory:
        root = Path(directory)
        settings = Settings(database_url=f"sqlite:///{root / 'benchmark.sqlite3'}", data_dir=root)
        engine = make_engine(settings.database_url)
        try:
            # Test-only isolated schema; production upgrades always use Alembic.
            Base.metadata.create_all(engine)
            repository = Repository(engine, settings=settings)
            collector = Collector(settings, ArtifactStore(settings, httpx.MockTransport(handle)))
            collector._visit = Mock(wraps=collector._visit)
            request = CollectRequest(
                url="https://cdn.modrinth.com/benchmark.jar",
                expected_hash=hashlib.sha256(payload).hexdigest(),
            )
            identifier = repository.enqueue(request)
            worker = Worker(settings, repository, collector)
            assert worker.run_once()
            elapsed = []
            for _ in range(iterations):
                start = time.perf_counter()
                assert repository.enqueue(request) == identifier
                elapsed.append((time.perf_counter() - start) * 1000)
            assert not worker.run_once()
            assert transfers == 1 and collector._visit.call_count == 1
            elapsed.sort()
            return {
                "scope": "SQLite local repository cache admission; excludes HTTP API/network",
                "iterations": iterations,
                "http_calls_including_initial": transfers,
                "parse_calls_including_initial": collector._visit.call_count,
                "tasks": len(repository.jobs()),
                "cache_p50_ms": round(statistics.median(elapsed), 3),
                "cache_p95_ms": round(elapsed[max(0, int(iterations * 0.95) - 1)], 3),
                "reserved_download_bytes": repository.status()["reserved_download_bytes"],
            }
        finally:
            engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 10000:
        parser.error("iterations must be 1..10000")
    result = json.dumps(benchmark(args.iterations), indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
