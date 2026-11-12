"""Rainyun/S3 via boto3; legacy profile/credential schema, isolated object prefix."""

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import Settings
from .contracts import CollectionError
from .storage import Checkpoint


class CloudProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    provider: Literal["rainyun", "s3"] = "rainyun"
    endpoint: str
    bucket: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
    region: str = Field(pattern=r"^[a-z0-9-]{1,63}$")
    prefix: str = Field(default="mirror", pattern=r"^[a-z0-9][a-z0-9/_-]{0,79}$")
    addressing_style: Literal["path", "virtual"] = "path"
    quota_gib: int = Field(default=80, ge=1, le=1000000)
    quota_unit: Literal["GB", "GiB"] = "GiB"
    daily_upload_gib: int = Field(default=1, ge=1, le=1000000)

    @model_validator(mode="after")
    def validate_location(self) -> "CloudProfile":
        url = urlsplit(self.endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or url.port not in (None, 443)
            or "\\" in self.endpoint
            or "//" in self.prefix
            or self.prefix.endswith("/")
        ):
            raise ValueError("invalid_cloud_location")
        return self

    @classmethod
    def load(cls, settings: Settings) -> "CloudProfile | None":
        if settings.cloud_profile_file is None:
            if settings.cloud_profile_id:
                raise CollectionError("cloud_profile_file_missing")
            return None
        try:
            payload = json.loads(settings.cloud_profile_file.read_text(encoding="utf-8-sig"))
            profiles = payload["qiniu_profiles"]
            matches = [p for p in profiles if p.get("id") == settings.cloud_profile_id]
            if len(matches) != 1:
                raise ValueError("ambiguous_profile")
            return cls.model_validate(matches[0])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise CollectionError("cloud_profile_invalid") from exc

    @property
    def storage_id(self) -> str:
        identity = [self.endpoint.rstrip("/"), self.bucket, self.object_prefix]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    @property
    def object_prefix(self) -> str:
        return f"{self.prefix}/bugg-source/v1"

    @property
    def capacity_bytes(self) -> int:
        return self.quota_gib * (1000**3 if self.quota_unit == "GB" else 1024**3)


class S3Archive:
    def __init__(self, settings: Settings, profile: CloudProfile, *, client: Any = None):
        self.profile = profile
        if client is not None:
            self.client = client
            return
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise CollectionError("install_bugg_source_s3_extra") from exc
        try:
            if settings.cloud_credentials_file is None:
                raise ValueError("missing_credentials")
            entries = json.loads(settings.cloud_credentials_file.read_text(encoding="utf-8-sig"))
            entry = entries[profile.id]
            access, secret = entry["access_key"], entry["secret_key"]
            if (
                not isinstance(access, str)
                or not isinstance(secret, str)
                or not access
                or not secret
            ):
                raise ValueError("invalid_credentials")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CollectionError("cloud_credentials_unavailable") from exc
        self.client = boto3.client(
            "s3",
            endpoint_url=profile.endpoint,
            region_name=profile.region,
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": profile.addressing_style},
                connect_timeout=5,
                read_timeout=30,
                max_pool_connections=4,
                retries={"total_max_attempts": 3, "mode": "standard"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def close(self) -> None:
        self.client.close()

    def _head(self, key: str) -> dict | None:
        from botocore.exceptions import ClientError

        try:
            return self.client.head_object(
                Bucket=self.profile.bucket, Key=key, ChecksumMode="ENABLED"
            )
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return None
            raise CollectionError("cloud_head_failed") from None

    def put_verified(
        self, path: Path, key: str, digest: str, size: int, checkpoint: Checkpoint
    ) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not key.startswith(
            self.profile.object_prefix + "/"
        ):
            raise CollectionError("invalid_archive_identity")
        checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        with path.open("rb") as source:
            before = path.stat()
            actual = hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                checkpoint()
                actual.update(chunk)
            if actual.hexdigest() != digest or before.st_size != size:
                raise CollectionError("archive_local_hash_mismatch")
            source.seek(0)
            checkpoint()
            try:
                existing = self._head(key)
                if existing is None:
                    self.client.put_object(
                        Bucket=self.profile.bucket,
                        Key=key,
                        Body=source,
                        ContentLength=size,
                        ChecksumSHA256=checksum,
                        ContentType="application/octet-stream",
                        Metadata={"sha256": digest},
                    )
                    checkpoint()
                    existing = self._head(key)
            except (BotoCoreError, ClientError) as exc:
                raise CollectionError("cloud_transfer_failed") from exc
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise CollectionError("archive_local_file_changed")
            if (
                not existing
                or existing.get("ContentLength") != size
                or existing.get("ChecksumSHA256") != checksum
            ):
                raise CollectionError("cloud_checksum_mismatch")
