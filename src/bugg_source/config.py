"""Process configuration; no implicit reads of another project's environment."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///data/bugg-source.sqlite3"
    data_dir: Path = Path("data")
    admin_token: str = field(default="", repr=False)
    platform_auth_url: str = ""
    platform_auth_token: str = field(default="", repr=False)
    public_url: str = ""
    cache_ingest_token: str = field(default="", repr=False)
    dependency_core_library: Path | None = None
    allowed_hosts: tuple[str, ...] = (
        "cdn.modrinth.com",
        "edge.forgecdn.net",
        "mediafilez.forgecdn.net",
        "media.forgecdn.net",
    )
    max_download_bytes: int = 256 * 1024 * 1024
    download_deadline: int = 180
    max_metadata_bytes: int = 1024 * 1024
    max_extracted_bytes: int = 128 * 1024 * 1024
    max_entries: int = 50000
    max_nested_jars: int = 128
    max_depth: int = 4
    lease_seconds: int = 600
    max_pending_jobs: int = 1000
    max_running_jobs: int = 4
    daily_download_bytes: int = 20 * 1024 * 1024 * 1024
    min_free_disk_bytes: int = 512 * 1024 * 1024
    cloud_profile_file: Path | None = None
    cloud_credentials_file: Path | None = None
    cloud_profile_id: str = ""

    def __post_init__(self) -> None:
        if self.cache_ingest_token and len(self.cache_ingest_token) < 32:
            raise ValueError("cache_ingest_token_too_short")
        if self.platform_auth_url:
            parsed = urlsplit(self.platform_auth_url)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in ("127.0.0.1", "::1")
                or parsed.username
                or parsed.password
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("platform_auth_url_must_be_loopback")
        for name, value in vars(self).items():
            if isinstance(value, int) and (value < 0 if name == "max_depth" else value <= 0):
                raise ValueError(f"{name}_invalid_limit")

    @classmethod
    def from_env(cls) -> "Settings":
        """Load explicit BUGG_* environment values and validate finite limits."""
        defaults = cls()
        limits = {
            name: int(os.getenv(f"BUGG_{name.upper()}", str(value)))
            for name, value in vars(defaults).items()
            if isinstance(value, int)
        }
        return cls(
            database_url=os.getenv("BUGG_DATABASE_URL", defaults.database_url),
            data_dir=Path(os.getenv("BUGG_DATA_DIR", "data")),
            admin_token=os.getenv("BUGG_ADMIN_TOKEN", ""),
            platform_auth_url=os.getenv("BUGG_PLATFORM_AUTH_URL", ""),
            platform_auth_token=os.getenv("BUGG_PLATFORM_AUTH_TOKEN", ""),
            public_url=os.getenv("BUGG_PUBLIC_URL", ""),
            cache_ingest_token=os.getenv("BUGG_CACHE_INGEST_TOKEN", ""),
            dependency_core_library=Path(os.environ["BUGG_DEPENDENCY_CORE_LIBRARY"])
            if os.getenv("BUGG_DEPENDENCY_CORE_LIBRARY")
            else None,
            cloud_profile_file=(
                Path(os.environ["BUGG_CLOUD_PROFILE_FILE"])
                if os.getenv("BUGG_CLOUD_PROFILE_FILE")
                else None
            ),
            cloud_credentials_file=(
                Path(os.environ["BUGG_CLOUD_CREDENTIALS_FILE"])
                if os.getenv("BUGG_CLOUD_CREDENTIALS_FILE")
                else None
            ),
            cloud_profile_id=os.getenv("BUGG_CLOUD_PROFILE_ID", ""),
            allowed_hosts=tuple(
                host.strip().lower()
                for host in os.getenv("BUGG_ALLOWED_HOSTS", ",".join(defaults.allowed_hosts)).split(
                    ","
                )
                if host.strip()
            ),
            **limits,
        )
