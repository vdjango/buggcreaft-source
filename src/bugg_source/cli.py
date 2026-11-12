"""Explicit composition and local batch entry points."""

import argparse
import json
from pathlib import Path

from alembic import command
from alembic.config import Config

from .archive_worker import ArchiveWorker
from .collector import Collector
from .config import Settings
from .contracts import CollectionError, CollectRequest
from .database import make_engine
from .repository import Repository
from .worker import Worker


def no_checkpoint() -> None:
    """Local CLI cancellation uses KeyboardInterrupt; no durable job is claimed."""


def main() -> None:
    parser = argparse.ArgumentParser(description="Bugg 真实制品依赖采集")
    parser.add_argument(
        "--env", type=Path, help="Explicit literal BUGG_/TRANSLATION_ configuration file"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("db-upgrade", help="Apply reviewed Alembic migrations")
    search_worker = commands.add_parser(
        "search-worker", help="Consume the hub feed and index text/vectors"
    )
    search_worker.add_argument("--once", action="store_true")
    search_rebuild = commands.add_parser("search-rebuild", help="Queue an explicit search reindex")
    search_rebuild.add_argument("--reset-source", action="store_true")
    embedding = commands.add_parser(
        "embedding-serve", help="Serve an explicitly prepared local ONNX model"
    )
    embedding.add_argument("--host", default="127.0.0.1")
    embedding.add_argument("--port", type=int, default=18452)
    platform = commands.add_parser("platform", help="Run migrated site, mirror, API and workers")
    platform.add_argument("--workers", type=int, default=1)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=18449)
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    archive_worker = commands.add_parser(
        "archive-worker", help="Upload queued local evidence to Rainyun/S3"
    )
    archive_worker.add_argument("--once", action="store_true")
    for name in ("archive-backfill", "archive-retry"):
        archive_command = commands.add_parser(name)
        archive_command.add_argument("--limit", type=int, default=100)
    local = commands.add_parser(
        "import-local", help="Copy JARs into local CAS, extract and persist"
    )
    local.add_argument("paths", nargs="+", type=Path)
    manifest = commands.add_parser(
        "enqueue-manifest", help="Enqueue publisher file URLs from JSONL"
    )
    manifest.add_argument("path", type=Path)
    manifest.add_argument("--limit", type=int, default=100)
    manifest.add_argument("--warmup", action="store_true")
    args = parser.parse_args()
    if args.env:
        from mc_translate.config import load_env

        if not args.env.is_file():
            parser.error("--env must name an existing file")
        load_env(args.env)
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "platform":
        from .platform import run_platform

        raise SystemExit(run_platform(workers=args.workers))
    if args.command == "db-upgrade":
        root = Path(__file__).resolve().parents[2]
        command.upgrade(Config(str(root / "alembic.ini")), "head")
        return
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return
    if args.command == "embedding-serve":
        import uvicorn

        from .search.inference import create_embedding_app

        uvicorn.run(create_embedding_app(), host=args.host, port=args.port)
        return
    engine = make_engine(settings.database_url)
    repository = Repository(engine, settings=settings)
    try:
        if args.command in ("search-worker", "search-rebuild"):
            from .search.clients import SearchClients
            from .search.config import SearchSettings
            from .search.repository import SearchRepository
            from .search.worker import SearchWorker

            search_settings = SearchSettings.from_env()
            search_repository = SearchRepository(engine, search_settings)
            search_repository.ready()
            if args.command == "search-rebuild":
                print(json.dumps(search_repository.rebuild(reset=args.reset_source)))
            else:
                clients = SearchClients(search_settings)
                try:
                    SearchWorker(search_repository, clients).run(once=args.once)
                finally:
                    clients.close()
        elif args.command == "worker":
            Worker(settings, repository).run(once=args.once)
        elif args.command == "archive-worker":
            uploader = ArchiveWorker(settings, repository)
            try:
                uploader.run(once=args.once)
            finally:
                uploader.archive.close()
        elif args.command in ("archive-backfill", "archive-retry"):
            if not 1 <= args.limit <= 1000:
                parser.error("--limit must be between 1 and 1000")
            count = (
                repository.backfill_archives(args.limit)
                if args.command == "archive-backfill"
                else repository.retry_archives(args.limit)
            )
            print(json.dumps({"scheduled": count}))
        elif args.command == "enqueue-manifest":
            if args.limit <= 0:
                parser.error("--limit must be positive")
            collector = Collector(settings)
            count = 0
            with args.path.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    if line.strip():
                        count += 1
                        if count > args.limit:
                            raise SystemExit("manifest_batch_limit_exceeded")
                        request = CollectRequest.model_validate_json(line)
                        if args.warmup:
                            request = request.model_copy(update={"purpose": "warmup"})
                        collector.store.validate_url(request.url)
                        print(repository.enqueue(request))
        elif args.command == "import-local":
            collector = Collector(settings)
            failures = 0
            for path in args.paths:
                candidates = sorted(path.rglob("*.jar")) if path.is_dir() else [path]
                for candidate in candidates:
                    try:
                        result = collector.import_local(
                            candidate, no_checkpoint, scan_lookup=repository.get_scan
                        )
                        repository.save_local_scan(result)
                        print(result.model_dump_json())
                    except (CollectionError, OSError) as exc:
                        failures += 1
                        print(
                            json.dumps(
                                {"file": str(candidate), "error": str(exc)}, ensure_ascii=False
                            )
                        )
            if failures:
                raise SystemExit(1)
    finally:
        engine.dispose()
