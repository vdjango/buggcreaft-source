"""Versioned facts. Raw names and dependency expressions must never be inferred."""

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PARSER_VERSION = "1.1.0"


class CollectionError(Exception):
    """Safe, stable error code suitable for task records."""


class Cancelled(CollectionError):
    """Cooperative cancellation; no successful result may be committed."""


class QueueFull(CollectionError):
    """Admission refused; existing shared jobs and completed caches remain readable."""


class CollectionPaused(CollectionError):
    """Administrative pause releases a lease without declaring the file failed."""


class ReleaseIntake(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["modrinth", "curseforge"]
    release_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    project_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    origin: Literal["request", "hotspot", "dependency"] = "request"
    url: str = Field(min_length=1, max_length=4096)
    digest: str
    algorithm: Literal["sha1", "sha256", "sha512"]
    size: int = Field(gt=0, le=256 * 1024 * 1024)
    game_versions: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=256
    )
    loaders: list[str] = Field(default_factory=list, max_length=32)
    dependencies: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    publisher_data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_publisher_data(self) -> "ReleaseIntake":
        data = self.publisher_data
        if data is not None:
            project = data.get("project_id") if self.provider == "modrinth" else data.get("modId")
            if str(data.get("id")) != self.release_id or str(project) != self.project_id:
                raise ValueError("publisher_release_identity_mismatch")
            if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
                raise ValueError("publisher_release_size_limit")
        return self

    def collection(self) -> "CollectRequest":
        return CollectRequest(
            url=self.url,
            expected_hash=self.digest,
            hash_algorithm=self.algorithm,
            source_provider=self.provider,
            source_file_id=self.release_id,
            purpose="on_demand" if self.origin == "request" else "warmup",
        )


class CollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096)
    expected_hash: str | None = None
    hash_algorithm: Literal["sha1", "sha256", "sha512"] = "sha256"
    source_provider: str | None = Field(default=None, max_length=64)
    source_file_id: str | None = Field(default=None, max_length=128)
    purpose: Literal["on_demand", "warmup"] = "on_demand"

    @model_validator(mode="after")
    def validate_digest(self) -> "CollectRequest":
        if self.expected_hash is not None:
            length = {"sha1": 40, "sha256": 64, "sha512": 128}[self.hash_algorithm]
            if not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", self.expected_hash):
                raise ValueError("invalid_expected_hash")
            self.expected_hash = self.expected_hash.lower()
        return self


class FactsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_hashes(self) -> "FactsQuery":
        if any(not re.fullmatch(r"[a-fA-F0-9]{64}", value) for value in self.sha256):
            raise ValueError("invalid_artifact_sha256")
        self.sha256 = list(dict.fromkeys(value.lower() for value in self.sha256))
        return self


class ReleaseIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["modrinth", "curseforge"]
    project_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    release_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ReleasesQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    releases: list[ReleaseIdentity] = Field(min_length=1, max_length=100)


class ShaderReviewDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reviewer: str = Field(min_length=1, max_length=128)
    conclusion: Literal["confirmed_missing", "verified_no_external", "needs_investigation"]
    note: str = Field(min_length=1, max_length=4096)


class Evidence(BaseModel):
    container_path: str
    member_path: str
    sha256: str
    size: int
    object_key: str


class Dependency(BaseModel):
    target_id: str
    relation: str
    version_raw: Any
    environment: str | None = None
    ordering: str | None = None
    source_path: str
    raw: Any


class ModFact(BaseModel):
    mod_id: str
    declared_name: str | None
    version_raw: str | None
    version_resolved: str | None
    loader: str
    container_path: str
    container_type: str | None = None
    metadata_path: str
    environment: str | None = None
    provides: list[Any] = Field(default_factory=list)
    loader_constraints: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[Dependency] = Field(default_factory=list)
    raw: dict[str, Any]


class Issue(BaseModel):
    code: str
    path: str
    detail: str = ""


class ScanResult(BaseModel):
    artifact_sha256: str
    size: int
    parser_version: str = PARSER_VERSION
    status: Literal["complete_declared", "partial", "unsupported"]
    mods: list[ModFact] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class JobView(BaseModel):
    id: str
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attempts: int
    created_at: str
    updated_at: str
    error: str | None
    artifact_sha256: str | None
    cancel_requested: bool
    source_provider: str | None
    source_file_id: str | None
    verification: Literal["unverified", "expected_hash_matched"]
    parser_version: str
    priority: int
    cache_hit: bool


class ArtifactView(BaseModel):
    scan_id: str
    artifact_sha256: str
    size: int
    parser_version: str
    status: Literal["complete_declared", "partial", "unsupported"]
    created_at: str


class ArtifactPage(BaseModel):
    items: list[ArtifactView]
    next_cursor: str | None
