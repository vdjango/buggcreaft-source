"""Validate the migrated platform's session without owning a second account store."""

import secrets

import httpx
from fastapi import HTTPException, Request

from .config import Settings


class AdminAuthorization:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client

    async def __call__(self, request: Request) -> None:
        token = request.headers.get("authorization", "")
        if token:
            expected = f"Bearer {self.settings.admin_token}"
            if not self.settings.admin_token or not secrets.compare_digest(
                token.encode(), expected.encode()
            ):
                raise HTTPException(401, "unauthorized")
            return
        if not self.settings.platform_auth_url or not self.settings.platform_auth_token:
            raise HTTPException(
                401 if self.settings.admin_token else 503,
                "unauthorized" if self.settings.admin_token else "admin_token_not_configured",
            )
        cookie = request.cookies.get("translation_admin", "")
        if not cookie or len(cookie) > 256 or any(c in cookie for c in '\r\n;"'):
            raise HTTPException(401, "unauthorized")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            allowed = {str(request.base_url).rstrip("/")}
            if self.settings.public_url:
                allowed.add(self.settings.public_url.rstrip("/"))
            if not origin or origin not in allowed:
                raise HTTPException(403, "invalid_origin")
        try:
            response = await self.client.get(
                self.settings.platform_auth_url.rstrip("/") + "/_internal/admin-session",
                headers={
                    "Cookie": "translation_admin=" + cookie,
                    "X-Platform-Auth": self.settings.platform_auth_token,
                },
            )
        except httpx.HTTPError as exc:
            raise HTTPException(503, "platform_auth_unavailable") from exc
        if response.status_code in (401, 403):
            raise HTTPException(401, "unauthorized")
        if response.status_code != 200:
            raise HTTPException(503, "platform_auth_unavailable")
        try:
            authenticated = response.json().get("authenticated") is True
        except (ValueError, AttributeError):
            authenticated = False
        if not authenticated:
            raise HTTPException(401, "unauthorized")
