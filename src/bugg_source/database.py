"""Schema owner. Runtime never silently creates or upgrades production tables."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "collection_jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "state", "priority", "created_at"),
        Index("ix_jobs_lease", "state", "lease_until"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    state: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(36))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(String(128))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), ForeignKey("artifacts.sha256"))
    parser_version: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    download_reservation_token: Mapped[str | None] = mapped_column(String(36))


class CollectionKey(Base):
    __tablename__ = "collection_keys"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("collection_jobs.id"))


class CollectionGate(Base):
    __tablename__ = "collection_gate"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    paused: Mapped[bool] = mapped_column(default=False)


class ReleaseObservation(Base):
    """Trusted publisher observations; immutable file identity, not package facts."""

    __tablename__ = "release_observations"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    release_id: Mapped[str] = mapped_column(String(128))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("collection_jobs.id"))
    origin: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicReleaseSnapshot(Base):
    """Append-only trusted publisher snapshots, independent of upstream cache eviction."""

    __tablename__ = "public_release_snapshots"
    __table_args__ = (Index("ix_public_release_identity", "provider", "project_id", "release_id"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    project_id: Mapped[str] = mapped_column(String(128))
    release_id: Mapped[str] = mapped_column(String(128))
    algorithm: Mapped[str] = mapped_column(String(16))
    digest: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ShaderReview(Base):
    __tablename__ = "shader_reviews"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    observation_key: Mapped[str] = mapped_column(String(64), ForeignKey("release_observations.key"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    state: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ShaderReviewGame(Base):
    __tablename__ = "shader_review_games"
    __table_args__ = (Index("ix_shader_review_game", "game_version", "review_id"),)
    review_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("shader_reviews.id"), primary_key=True
    )
    game_version: Mapped[str] = mapped_column(String(128), primary_key=True)


class ShaderReviewDecision(Base):
    __tablename__ = "shader_review_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(64), ForeignKey("shader_reviews.id"))
    reviewer: Mapped[str] = mapped_column(String(128))
    conclusion: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(4096))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DownloadBudget(Base):
    __tablename__ = "download_budgets"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger)


class ArtifactHash(Base):
    __tablename__ = "artifact_hashes"
    algorithm: Mapped[str] = mapped_column(String(16), primary_key=True)
    digest: Mapped[str] = mapped_column(String(128), primary_key=True)
    artifact_sha256: Mapped[str] = mapped_column(String(64), ForeignKey("artifacts.sha256"))


class ArchiveObject(Base):
    __tablename__ = "archive_objects"
    __table_args__ = (Index("ix_archive_claim", "storage_id", "state", "created_at"),)
    storage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    local_key: Mapped[str] = mapped_column(String(256))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer)
    capacity_reserved: Mapped[bool] = mapped_column(default=False)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArchiveSpace(Base):
    __tablename__ = "archive_spaces"
    storage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger)


class UploadBudget(Base):
    __tablename__ = "upload_budgets"
    storage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger)


class Artifact(Base):
    __tablename__ = "artifacts"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Scan(Base):
    __tablename__ = "scans"
    __table_args__ = (
        UniqueConstraint("artifact_sha256", "parser_version", name="uq_scan_parser"),
        Index("ix_scans_browse", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    artifact_sha256: Mapped[str] = mapped_column(String(64), ForeignKey("artifacts.sha256"))
    parser_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CachedArtifact(Base):
    """Explicit publication, independent of private imports and immutable scan snapshots."""

    __tablename__ = "cached_artifacts"
    sha256: Mapped[str] = mapped_column(
        String(64), ForeignKey("artifacts.sha256"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    public: Mapped[bool] = mapped_column(default=False)
    job_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("collection_jobs.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:"):
        engine = create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
        event.listen(engine, "connect", enable_sqlite_foreign_keys)
        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def enable_sqlite_foreign_keys(connection: Any, _: Any) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
