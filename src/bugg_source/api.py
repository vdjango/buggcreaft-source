"""FastAPI transport; all blocking endpoints use framework thread-pool dispatch."""

import asyncio
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from .auth import AdminAuthorization
from .config import Settings
from .contracts import (
    PARSER_VERSION,
    ArtifactPage,
    CollectionError,
    CollectRequest,
    FactsQuery,
    JobView,
    QueueFull,
    ReleaseIntake,
    ReleasesQuery,
    ScanResult,
    ShaderReviewDecisionInput,
)
from .database import make_engine
from .dependency_core import DependencyCore
from .repository import Repository
from .search.clients import SearchClients
from .search.config import SearchSettings
from .search.repository import SearchRepository
from .search.routes import search_router
from .search.service import SearchService
from .storage import ArtifactStore


def create_app(
    settings: Settings | None = None, search_settings: SearchSettings | None = None
) -> FastAPI:
    configuration = settings or Settings.from_env()
    configuration.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(configuration.database_url)
    repository = Repository(engine, settings=configuration)
    store = ArtifactStore(configuration)
    dependency_core = DependencyCore(configuration.dependency_core_library)
    upload_slots = asyncio.Semaphore(min(2, configuration.max_running_jobs))
    auth_client = httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False)
    authorize = AdminAuthorization(configuration, auth_client)
    search_clients = SearchClients(search_settings or SearchSettings.from_env())
    search = SearchService(SearchRepository(engine, search_clients.settings), search_clients)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            search_clients.close()
            await auth_client.aclose()
            engine.dispose()

    app = FastAPI(title="Bugg 源站制品依赖 API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(search_router(search, authorize))

    @app.get("/health")
    def health():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM collection_jobs LIMIT 1"))
        return {"status": "ready", "parser_version": PARSER_VERSION}

    @app.post(
        "/api/v1/collections",
        status_code=202,
        response_model=JobView,
        dependencies=[Depends(authorize)],
    )
    def enqueue(request: CollectRequest, response: Response):
        try:
            store.validate_url(request.url)
        except CollectionError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            identifier = repository.enqueue(request)
        except QueueFull as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc
        result = repository.get_job(identifier)
        if result["state"] == "succeeded":
            response.status_code = 200
        return result

    @app.get("/api/v1/collections", response_model=list[JobView], dependencies=[Depends(authorize)])
    def jobs(limit: Annotated[int, Query(ge=1, le=100)] = 50):
        return repository.jobs(limit)

    @app.get("/api/v1/storage/status", dependencies=[Depends(authorize)])
    def storage_status():
        return repository.status()

    @app.get("/api/v1/artifacts", response_model=ArtifactPage, dependencies=[Depends(authorize)])
    def artifacts(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
    ):
        try:
            return repository.artifacts(limit, cursor)
        except CollectionError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get(
        "/api/v1/collections/{identifier}",
        response_model=JobView,
        dependencies=[Depends(authorize)],
    )
    def job(identifier: str):
        result = repository.get_job(identifier)
        if result is None:
            raise HTTPException(404, "job_not_found")
        return result

    @app.post(
        "/api/v1/collections/{identifier}/cancel",
        response_model=JobView,
        dependencies=[Depends(authorize)],
    )
    def cancel(identifier: str):
        if not repository.cancel(identifier):
            raise HTTPException(404, "job_not_found")
        return repository.get_job(identifier)

    @app.post(
        "/api/v1/collections/{identifier}/retry",
        status_code=202,
        response_model=JobView,
        dependencies=[Depends(authorize)],
    )
    def retry(identifier: str):
        try:
            new_id = repository.retry(identifier)
        except QueueFull as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc
        if new_id is None:
            raise HTTPException(409, "job_not_retryable")
        return repository.get_job(new_id)

    @app.get(
        "/api/v1/artifacts/{digest}/dependencies",
        response_model=ScanResult,
        dependencies=[Depends(authorize)],
    )
    def dependencies(digest: str, parser_version: str = PARSER_VERSION):
        result = repository.get_scan(digest, parser_version)
        if result is None:
            raise HTTPException(404, "scan_not_found")
        return result

    def cache_authorize(request: Request) -> None:
        if not configuration.cache_ingest_token:
            raise HTTPException(503, "cache_ingest_not_configured")
        expected = "Bearer " + configuration.cache_ingest_token
        if not secrets.compare_digest(
            request.headers.get("Authorization", "").encode(), expected.encode()
        ):
            raise HTTPException(403, "cache_ingest_forbidden")

    @app.get("/api/v1/dependency-collection/control", dependencies=[Depends(authorize)])
    def collection_control():
        return repository.collection_control()

    @app.post("/api/v1/dependency-collection/control", dependencies=[Depends(authorize)])
    def set_collection_control(body: dict[str, bool]):
        if set(body) != {"paused"} or type(body["paused"]) is not bool:
            raise HTTPException(422, "invalid_collection_control")
        return repository.collection_control(body["paused"])

    @app.post("/api/v1/internal/dependency-releases", dependencies=[Depends(cache_authorize)])
    def observe_release(body: ReleaseIntake):
        try:
            request = body.collection()
            store.validate_url(request.url)
            return repository.observe_release(body)
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc
        except (CollectionError, ValueError) as exc:
            raise HTTPException(422, "invalid_release_observation") from exc

    @app.post(
        "/api/v1/internal/dependency-jobs/{identifier}/retry",
        dependencies=[Depends(cache_authorize)],
    )
    def retry_dependency_job(identifier: str):
        replacement = repository.retry(identifier)
        if replacement is None:
            raise HTTPException(409, "dependency_job_not_retryable")
        return repository.get_job(replacement)

    @app.post("/api/v1/dependency-checks", dependencies=[Depends(authorize)])
    def dependency_check(body: dict):
        try:
            query = FactsQuery.model_validate({"sha256": body.get("sha256")})
            environment = body["environment"]
            if not isinstance(environment, dict) or set(environment) - {
                "minecraft",
                "loader",
                "loader_version",
                "java",
            }:
                raise ValueError("invalid_dependency_environment")
            if any(not isinstance(v, str) or len(v) > 128 for v in environment.values()):
                raise ValueError("invalid_dependency_environment")
            rows = repository.public_scans(query.sha256)["items"]
            if any("facts" not in row for row in rows):
                return dict(state="pending_facts", items=rows)
            return dependency_core.evaluate(
                dict(
                    environment=environment,
                    artifacts=[dict(sha256=row["sha256"], facts=row["facts"]) for row in rows],
                )
            )
        except CollectionError as exc:
            raise HTTPException(
                503 if str(exc) == "dependency_core_unavailable" else 422, str(exc)
            ) from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, "invalid_dependency_check") from exc

    @app.get("/api/v1/shader-reviews", dependencies=[Depends(authorize)])
    def shader_reviews(
        game_version: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        return {"items": repository.shader_reviews(game_version, limit)}

    @app.post("/api/v1/shader-reviews/{review_id}/decisions", dependencies=[Depends(authorize)])
    def decide_shader_review(
        review_id: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
        body: ShaderReviewDecisionInput,
    ):
        if not repository.decide_shader_review(
            review_id, body.reviewer, body.conclusion, body.note
        ):
            raise HTTPException(404, "shader_review_not_found")
        return {"recorded": True}

    @app.get("/api/v1/dependency-facts/capabilities")
    def facts_capabilities():
        return dict(
            schema="bugg-dependency-facts-v1",
            parser_version=PARSER_VERSION,
            max_batch=100,
            max_response_bytes=8 * 1024 * 1024,
        )

    @app.post("/api/v1/dependency-facts/query")
    async def query_facts(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16 * 1024:
                raise HTTPException(413, "facts_query_size_limit")
        try:
            query = FactsQuery.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(422, "invalid_facts_query") from exc
        return await run_in_threadpool(repository.public_scans, query.sha256)

    @app.post("/api/v1/dependency-releases/query")
    async def query_dependency_releases(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 64 * 1024:
                raise HTTPException(413, "releases_query_size_limit")
        try:
            query = ReleasesQuery.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(422, "invalid_releases_query") from exc
        return await run_in_threadpool(
            repository.public_release_snapshots,
            [identity.model_dump() for identity in query.releases],
        )

    @app.get("/api/v1/internal/cached-artifacts/{digest}", dependencies=[Depends(cache_authorize)])
    def cached_state(digest: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")]):
        return dict(
            repository.cached_state(digest),
            blob_available=(store.root / "blobs" / f"{digest}.jar").is_file(),
        )

    @app.put("/api/v1/internal/cached-artifacts/{digest}", dependencies=[Depends(cache_authorize)])
    async def cached_upload(
        request: Request,
        digest: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
        kind: Literal["jar", "archive", "other"],
        visibility: Literal["public", "private"],
        size: Annotated[int, Query(ge=0, le=2**63 - 1)],
        reuse: bool = False,
    ):
        try:
            if kind != "other":
                if upload_slots.locked():
                    raise HTTPException(
                        429, "cache_upload_slots_full", headers={"Retry-After": "5"}
                    )
                async with upload_slots:
                    if reuse:
                        local = await run_in_threadpool(
                            store.cached_blob,
                            CollectRequest(url=f"cache://sha256/{digest}", expected_hash=digest),
                            digest,
                            lambda: None,
                        )
                        received = local.stat().st_size
                    else:
                        received = await store.receive(request.stream(), digest, size)
                if received != size:
                    raise CollectionError("cached_artifact_size_mismatch")
            else:
                async for chunk in request.stream():
                    if chunk:
                        raise HTTPException(413, "nonjar_upload_not_required")
            return await run_in_threadpool(
                repository.enqueue_cached, digest, size, kind=kind, public=visibility == "public"
            )
        except QueueFull as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc
        except CollectionError as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
