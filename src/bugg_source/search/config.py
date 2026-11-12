"""Explicit mesh and inference settings; endpoints never come from callers."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SearchSettings:
    enabled: bool = False
    source_url: str = "http://127.0.0.1:18448"
    source_token: str = field(default="", repr=False)
    embedding_url: str = ""
    embedding_token: str = field(default="", repr=False)
    model_revision: str = ""
    dimensions: int = 512
    node_id: str = "local"

    def __post_init__(self):
        for value in (self.source_url, self.embedding_url):
            if not value:
                continue
            url = urlsplit(value)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
                or url.path not in ("", "/")
                or url.query
                or url.fragment
                or url.scheme == "http"
                and url.hostname not in ("127.0.0.1", "::1")
            ):
                raise ValueError("search_endpoint_requires_https_or_loopback")
        if not 1 <= self.dimensions <= 2000 or len(self.node_id) > 100:
            raise ValueError("invalid_search_configuration")
        if self.embedding_url and (not self.model_revision or not self.embedding_token):
            raise ValueError("embedding_revision_and_token_required")

    @classmethod
    def from_env(cls):
        return cls(
            enabled=os.getenv("BUGG_SEARCH_ENABLED") == "1",
            source_url=os.getenv("BUGG_SEARCH_SOURCE_URL", "http://127.0.0.1:18448"),
            source_token=os.getenv("BUGG_PLATFORM_AUTH_TOKEN", ""),
            embedding_url=os.getenv("BUGG_EMBEDDING_URL", ""),
            embedding_token=os.getenv("BUGG_EMBEDDING_TOKEN", ""),
            model_revision=os.getenv("BUGG_EMBEDDING_REVISION", ""),
            dimensions=int(os.getenv("BUGG_EMBEDDING_DIMENSIONS", "512")),
            node_id=os.getenv("BUGG_NODE_ID", "local"),
        )
