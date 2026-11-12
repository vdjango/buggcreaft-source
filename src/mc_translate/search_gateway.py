"""Approved read mirrors reuse federation; only the hub calls its fixed search API."""

import hashlib
import json
import os
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientTimeout, web


class SearchGateway:
    def __init__(self, app):
        self.app = app
        self.origin = os.environ.get("BUGG_SEARCH_API_URL", "http://127.0.0.1:18449").rstrip("/")
        parsed = urlsplit(self.origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("127.0.0.1", "::1")
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("search_gateway_requires_loopback")

    async def read(self, request):
        node = request.get("federation_node")
        self.app["api"].rate(request, "search", 1200 if node else 120)
        identity = (
            "node:" + node
            if node
            else "client:" + hashlib.sha256(self.app["api"].ip(request).encode()).hexdigest()
        )
        headers = {
            "X-Search-Identity": identity,
            "X-Platform-Auth": os.environ.get("BUGG_PLATFORM_AUTH_TOKEN", ""),
        }
        if len(request.query_string) > 3000:
            return web.json_response({"detail": "invalid_search_query"}, status=400)
        tail = request.path.removeprefix("/v1/search")
        target = "/api/v1/search" + tail if tail in ("", "/capabilities") else "/api/v1" + tail
        try:
            async with self.app["http"].get(
                self.origin + target,
                params=request.query,
                headers=headers,
                allow_redirects=False,
                timeout=ClientTimeout(total=20),
            ) as response:
                data = bytearray()
                async for block in response.content.iter_chunked(65536):
                    data.extend(block)
                    if len(data) > 2 * 1024 * 1024:
                        return web.json_response(
                            {"detail": "search_response_too_large"}, status=503
                        )
                json.loads(data)
                return web.Response(
                    body=bytes(data),
                    status=response.status,
                    content_type="application/json",
                    headers={"Cache-Control": "no-store"},
                )
        except (ClientError, TimeoutError, ValueError):
            return web.json_response({"detail": "search_unavailable"}, status=503)
