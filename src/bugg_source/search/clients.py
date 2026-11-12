"""Bounded fixed-endpoint mesh clients; no implicit redirects or ambient proxy."""

import json
import math

import httpx

from .config import SearchSettings


class SearchUnavailable(Exception):
    pass


class SearchClients:
    def __init__(self, settings: SearchSettings):
        self.settings = settings
        self.http = httpx.Client(
            timeout=httpx.Timeout(15, connect=3),
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=8),
        )

    def call(self, origin: str, path: str, *, body=None, embedding=False):
        if not origin:
            raise SearchUnavailable(
                "semantic_unavailable" if embedding else "search_authority_unavailable"
            )
        header = (
            {"X-Embedding-Key": self.settings.embedding_token}
            if embedding
            else {"X-Platform-Auth": self.settings.source_token}
        )
        try:
            with self.http.stream(
                "GET" if body is None else "POST", origin + path, json=body, headers=header
            ) as response:
                response.raise_for_status()
                data = bytearray()
                for block in response.iter_bytes():
                    data.extend(block)
                    if len(data) > 32 * 1024 * 1024:
                        raise SearchUnavailable("search_response_too_large")
                return json.loads(data)
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchUnavailable(
                "semantic_unavailable" if embedding else "search_authority_unavailable"
            ) from exc

    def validate(self, projects: list[str], *, epoch: str | None = None) -> dict:
        value = self.call(
            self.settings.source_url, "/_internal/search/validate", body={"projects": projects}
        )
        if epoch is not None and value["epoch"] != epoch:
            raise SearchUnavailable("search_source_epoch_changed_reinitialize_required")
        return value["projects"]

    def embed(self, texts: list[str], *, query=False) -> list[list[float]]:
        value = self.call(
            self.settings.embedding_url,
            "/v1/embeddings",
            body=dict(texts=texts, query=query, revision=self.settings.model_revision),
            embedding=True,
        )
        vectors = value.get("vectors", [])
        if value.get("revision") != self.settings.model_revision or len(vectors) != len(texts):
            raise SearchUnavailable("embedding_space_mismatch")
        for vector in vectors:
            if (
                len(vector) != self.settings.dimensions
                or any(not math.isfinite(n) for n in vector)
                or not 0.99 <= sum(n * n for n in vector) <= 1.01
            ):
                raise SearchUnavailable("embedding_space_mismatch")
        return vectors

    def close(self):
        self.http.close()
