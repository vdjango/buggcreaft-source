"""Public read API separated from authenticated indexing/visibility controls."""

import hmac
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from .clients import SearchUnavailable


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reset_source: bool = False


class VisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    visible: bool


def search_router(service, authorize):
    router = APIRouter(prefix="/api/v1")

    def rate(request):
        secret = service.clients.settings.source_token
        trusted = bool(secret) and hmac.compare_digest(
            request.headers.get("X-Platform-Auth", ""), secret
        )
        identity = request.headers.get("X-Search-Identity", "") if trusted else ""
        if not identity or len(identity) > 150:
            identity = request.client.host if request.client else "unknown"
        service.repository.rate(
            identity, limit=1200 if trusted and identity.startswith("node:") else 120
        )

    def fail(exc):
        code = str(exc) if not isinstance(exc, SQLAlchemyError) else "search_database_unavailable"
        status = (
            429
            if code == "search_rate_limited"
            else 410
            if code == "search_session_expired_or_invalid"
            else 503
        )
        raise HTTPException(status, code) from exc

    @router.get("/search/capabilities")
    def capabilities():
        try:
            return service.capabilities()
        except (SearchUnavailable, SQLAlchemyError) as exc:
            fail(exc)

    @router.get("/search")
    def search(
        request: Request,
        q: Annotated[str, Query(max_length=200)] = "",
        mode: Literal["lexical", "semantic", "hybrid"] = "hybrid",
        resource_type: Literal["mod", "modpack", "resourcepack", "shader"] | None = None,
        source: Literal["modrinth", "curseforge"] | None = None,
        mc_version: Annotated[str | None, Query(max_length=80)] = None,
        loader: Annotated[str | None, Query(max_length=30)] = None,
        language: Annotated[str, Query(max_length=30)] = "zh-Hans",
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Query(max_length=100)] = None,
    ):
        try:
            service.repository.ready()
            rate(request)
            return service.search(
                q.strip(),
                mode,
                dict(
                    resource_type=resource_type,
                    source=source,
                    mc_version=mc_version,
                    loader=loader,
                    language=language,
                ),
                limit=limit,
                cursor=cursor,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (SearchUnavailable, SQLAlchemyError) as exc:
            fail(exc)

    @router.get("/projects/{project_id}/similar")
    def similar(
        project_id: str,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        cursor: Annotated[str | None, Query(max_length=100)] = None,
    ):
        try:
            service.repository.ready()
            rate(request)
            return service.similar(project_id, dict(language="zh-Hans"), limit, cursor)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (SearchUnavailable, SQLAlchemyError) as exc:
            fail(exc)

    @router.get("/admin/search/status", dependencies=[Depends(authorize)])
    def status():
        try:
            return service.repository.status()
        except (SearchUnavailable, SQLAlchemyError) as exc:
            fail(exc)

    @router.post("/admin/search/rebuilds", status_code=202, dependencies=[Depends(authorize)])
    def rebuild(body: RebuildRequest):
        try:
            return service.repository.rebuild(reset=body.reset_source)
        except (SearchUnavailable, SQLAlchemyError) as exc:
            fail(exc)

    @router.post("/admin/search/visibility", dependencies=[Depends(authorize)])
    def visibility(body: VisibilityRequest):
        try:
            return service.clients.call(
                service.clients.settings.source_url,
                "/_internal/search/visibility",
                body=body.model_dump(),
            )
        except SearchUnavailable as exc:
            fail(exc)

    return router
