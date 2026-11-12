"""Durable queue and immutable scan repository; each operation owns a short transaction."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from importlib.resources import files
from typing import Any
from uuid import uuid4

from sqlalchemy import Text, and_, cast, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .cloud import CloudProfile
from .config import Settings
from .contracts import (
    PARSER_VERSION,
    Cancelled,
    CollectionError,
    CollectionPaused,
    CollectRequest,
    QueueFull,
    ReleaseIntake,
    ScanResult,
)
from .database import (
    ArchiveObject,
    ArchiveSpace,
    Artifact,
    ArtifactHash,
    CachedArtifact,
    CollectionGate,
    CollectionKey,
    DownloadBudget,
    Job,
    PublicReleaseSnapshot,
    ReleaseObservation,
    Scan,
    ShaderReview,
    ShaderReviewDecision,
    ShaderReviewGame,
    UploadBudget,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


@lru_cache(maxsize=1)
def shader_core_projects() -> dict[str, frozenset[str]]:
    manifest = json.loads(
        files("mc_translate").joinpath("contracts/shader-runtimes.json").read_text(encoding="utf-8")
    )
    return {
        provider: frozenset(str(row[field]) for row in manifest["runtimes"] if row.get(field))
        for provider, field in (("modrinth", "projectId"), ("curseforge", "curseforgeProjectId"))
    }


class Repository:
    def __init__(
        self,
        engine: Engine,
        lease_seconds: int = 600,
        *,
        settings: Settings | None = None,
        parser_version: str = PARSER_VERSION,
    ):
        self.engine = engine
        self.settings = settings or Settings(lease_seconds=lease_seconds)
        self.lease_seconds = self.settings.lease_seconds
        self.parser_version = parser_version
        self.insert = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
        self.cloud = CloudProfile.load(self.settings)

    def _gate(self, session: Session) -> None:
        """A short DB write serializes admission across processes, including SQLite writers."""
        session.execute(
            self.insert(CollectionGate).values(id=1, revision=0).on_conflict_do_nothing()
        )
        session.execute(
            update(CollectionGate)
            .where(CollectionGate.id == 1)
            .values(revision=CollectionGate.revision + 1)
        )

    def _fingerprint(self, request: CollectRequest) -> str:
        identity = [self.parser_version, request.hash_algorithm, request.expected_hash]
        if not request.expected_hash or request.hash_algorithm == "sha1":
            identity.append(request.url)
        return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()

    @staticmethod
    def _known_artifact(session: Session, request: CollectRequest) -> str | None:
        if not request.expected_hash or request.hash_algorithm == "sha1":
            return None
        if request.hash_algorithm == "sha256":
            return session.scalar(
                select(Artifact.sha256).where(Artifact.sha256 == request.expected_hash)
            )
        return session.scalar(
            select(ArtifactHash.artifact_sha256).where(
                ArtifactHash.algorithm == request.hash_algorithm,
                ArtifactHash.digest == request.expected_hash,
            )
        )

    def known_artifact(self, request: CollectRequest) -> str | None:
        with Session(self.engine) as session:
            return self._known_artifact(session, request)

    def enqueue(self, request: CollectRequest) -> str:
        identifier, now = str(uuid4()), utcnow()
        fingerprint = self._fingerprint(request)
        priority = 100 if request.purpose == "on_demand" else 10
        with Session(self.engine) as session, session.begin():
            self._gate(session)
            key = session.get(CollectionKey, fingerprint)
            existing = session.get(Job, key.job_id) if key else None
            if (
                existing
                and existing.state in ("queued", "running")
                and not existing.cancel_requested
            ):
                if existing.state == "queued" and priority > existing.priority:
                    existing.priority = priority
                    existing.updated_at = now
                return existing.id
            digest = self._known_artifact(session, request)
            cached = (
                digest is not None
                and session.scalar(
                    select(Scan.id).where(
                        Scan.artifact_sha256 == digest, Scan.parser_version == self.parser_version
                    )
                )
                is not None
            )
            if cached and existing and existing.state == "succeeded":
                return existing.id
            if not cached:
                pending = session.scalars(
                    select(Job.id)
                    .where(Job.state.in_(("queued", "running")))
                    .limit(self.settings.max_pending_jobs)
                ).all()
                if len(pending) >= self.settings.max_pending_jobs:
                    raise QueueFull("collection_queue_full")
            session.add(
                Job(
                    id=identifier,
                    request=request.model_dump(),
                    state="succeeded" if cached else "queued",
                    created_at=now,
                    updated_at=now,
                    attempts=0,
                    cancel_requested=False,
                    parser_version=self.parser_version,
                    priority=priority,
                    cache_hit=bool(cached),
                    artifact_sha256=digest if cached else None,
                )
            )
            session.flush()
            session.execute(
                self.insert(CollectionKey)
                .values(fingerprint=fingerprint, job_id=identifier)
                .on_conflict_do_update(
                    index_elements=[CollectionKey.fingerprint], set_={"job_id": identifier}
                )
            )
        return identifier

    def jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))
            return [self._view(row) for row in rows]

    def get_job(self, identifier: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            job = session.get(Job, identifier)
            return self._view(job) if job else None

    @staticmethod
    def _view(job: Job) -> dict[str, Any]:
        # Signed query strings and credentials must not be returned in task views.
        return {
            "id": job.id,
            "state": job.state,
            "attempts": job.attempts,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "error": job.error,
            "artifact_sha256": job.artifact_sha256,
            "cancel_requested": job.cancel_requested,
            "parser_version": job.parser_version,
            "priority": job.priority,
            "cache_hit": job.cache_hit,
            "source_provider": job.request.get("source_provider"),
            "source_file_id": job.request.get("source_file_id"),
            "verification": (
                "expected_hash_matched"
                if job.state == "succeeded" and job.request.get("expected_hash")
                else "unverified"
            ),
        }

    def cancel(self, identifier: str) -> bool:
        with Session(self.engine) as session, session.begin():
            job = session.scalar(select(Job).where(Job.id == identifier).with_for_update())
            if not job:
                return False
            if job.state in ("queued", "running"):
                job.cancel_requested = True
                job.updated_at = utcnow()
                if job.state == "queued":
                    job.state = "cancelled"
            return True

    def retry(self, identifier: str) -> str | None:
        with Session(self.engine) as session:
            job = session.get(Job, identifier)
            if not job or job.state not in ("failed", "cancelled"):
                return None
            request = CollectRequest.model_validate(job.request)
        replacement = self.enqueue(request)
        with Session(self.engine) as session, session.begin():
            session.execute(
                update(ReleaseObservation)
                .where(ReleaseObservation.job_id == identifier)
                .values(job_id=replacement)
            )
        return replacement

    def claim(self) -> tuple[str, str, CollectRequest] | None:
        now = utcnow()
        eligible = or_(Job.state == "queued", and_(Job.state == "running", Job.lease_until < now))
        with Session(self.engine) as session, session.begin():
            self._gate(session)
            if session.get(CollectionGate, 1).paused:
                return None
            session.execute(
                update(Job)
                .where(
                    eligible,
                    Job.cancel_requested.is_(True),
                )
                .values(state="cancelled", claim_token=None, lease_until=None, updated_at=now)
            )
            session.execute(
                update(Job)
                .where(
                    eligible,
                    Job.attempts >= 3,
                )
                .values(
                    state="failed",
                    error="lease_retry_limit",
                    claim_token=None,
                    lease_until=None,
                    updated_at=now,
                )
            )
            running = session.scalars(
                select(Job.id)
                .where(Job.state == "running", Job.lease_until > now)
                .limit(self.settings.max_running_jobs)
            ).all()
            if len(running) >= self.settings.max_running_jobs:
                return None
            job = session.scalar(
                select(Job)
                .where(
                    eligible,
                    Job.attempts < 3,
                    Job.cancel_requested.is_(False),
                    Job.parser_version == self.parser_version,
                )
                .order_by(Job.priority.desc(), Job.created_at, Job.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not job:
                return None
            token = str(uuid4())
            job.state, job.claim_token = "running", token
            job.attempts += 1
            job.updated_at = now
            job.lease_until = now + timedelta(seconds=self.lease_seconds)
            return job.id, token, CollectRequest.model_validate(job.request)

    def reserve_download(self, identifier: str, token: str) -> None:
        """Conservatively reserve a full attempt before HTTP; no refunds after crashes."""
        now = utcnow()
        day = now.date().isoformat()
        with Session(self.engine) as session, session.begin():
            # Write first also avoids a read-to-write upgrade race in local SQLite.
            session.execute(
                self.insert(DownloadBudget)
                .values(day=day, reserved_bytes=0)
                .on_conflict_do_nothing()
            )
            job = session.scalar(select(Job).where(Job.id == identifier).with_for_update())
            lease_until = job.lease_until if job else None
            if lease_until is not None and lease_until.tzinfo is None:
                lease_until = lease_until.replace(tzinfo=UTC)
            if (
                not job
                or job.state != "running"
                or job.claim_token != token
                or job.cancel_requested
                or lease_until is None
                or lease_until <= now
            ):
                raise Cancelled("cancelled_or_lease_lost")
            if job.download_reservation_token == token:
                return
            changed = session.execute(
                update(DownloadBudget)
                .where(
                    DownloadBudget.day == day,
                    DownloadBudget.reserved_bytes + self.settings.max_download_bytes
                    <= self.settings.daily_download_bytes,
                )
                .values(
                    reserved_bytes=DownloadBudget.reserved_bytes + self.settings.max_download_bytes
                )
            )
            if changed.rowcount != 1:
                raise CollectionError("daily_download_budget_exhausted")
            job.download_reservation_token = token

    def pulse(self, identifier: str, token: str) -> None:
        now = utcnow()
        with Session(self.engine) as session, session.begin():
            gate = session.get(CollectionGate, 1)
            if gate and gate.paused:
                raise CollectionPaused("dependency_collection_paused")
            changed = session.execute(
                update(Job)
                .where(
                    Job.id == identifier,
                    Job.claim_token == token,
                    Job.state == "running",
                    Job.cancel_requested.is_(False),
                    Job.lease_until > now,
                )
                .values(updated_at=now, lease_until=now + timedelta(seconds=self.lease_seconds))
            )
            if changed.rowcount != 1:
                raise Cancelled("cancelled_or_lease_lost")

    def collection_control(self, paused: bool | None = None) -> dict[str, Any]:
        with Session(self.engine) as session, session.begin():
            self._gate(session)
            gate = session.get(CollectionGate, 1)
            if paused is not None:
                gate.paused = paused
            counts = dict(
                session.execute(select(Job.state, func.count()).group_by(Job.state)).all()
            )
            return dict(paused=gate.paused, states=counts, parser_version=self.parser_version)

    def observe_release(self, observation: ReleaseIntake) -> dict[str, Any]:
        """Trusted intake shares the existing queue; repeated observations do not reparse bytes."""
        request = observation.collection()
        key = self._fingerprint(request)
        known_core = observation.project_id in shader_core_projects()[observation.provider]
        payload = observation.model_dump(exclude={"url", "origin"})
        payload["digest"] = request.expected_hash
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        snapshot_id = hashlib.sha256(canonical.encode()).hexdigest()
        now = utcnow()
        # Persist the public publisher observation even if the collector queue is full.
        with Session(self.engine) as session, session.begin():
            session.execute(
                self.insert(PublicReleaseSnapshot)
                .values(
                    id=snapshot_id,
                    provider=observation.provider,
                    project_id=observation.project_id,
                    release_id=observation.release_id,
                    algorithm=observation.algorithm,
                    digest=request.expected_hash,
                    payload=payload,
                    created_at=now,
                    last_seen=now,
                )
                .on_conflict_do_update(
                    index_elements=[PublicReleaseSnapshot.id], set_={"last_seen": now}
                )
            )
        with Session(self.engine) as session:
            previous = session.get(ReleaseObservation, key)
            identifier = previous.job_id if previous else None
        if identifier is None:
            identifier = self.enqueue(request)
        with Session(self.engine) as session, session.begin():
            session.execute(
                self.insert(ReleaseObservation)
                .values(
                    key=key,
                    provider=observation.provider,
                    release_id=observation.release_id,
                    job_id=identifier,
                    origin=observation.origin,
                    payload=observation.model_dump(),
                    created_at=utcnow(),
                )
                .on_conflict_do_update(
                    index_elements=[ReleaseObservation.key], set_={"job_id": identifier}
                )
            )
            required = any(
                dep.get("dependency_type") == "required" or dep.get("relationType") == 3
                for dep in observation.dependencies
            )
            if known_core and not required:
                identity = [
                    observation.provider,
                    observation.project_id,
                    observation.release_id,
                    observation.algorithm,
                    observation.digest.lower(),
                ]
                review_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                now = utcnow()
                session.execute(
                    self.insert(ShaderReview)
                    .values(
                        id=review_id,
                        observation_key=key,
                        payload=observation.model_dump(),
                        state="pending",
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing()
                )
                for game in set(observation.game_versions):
                    session.execute(
                        self.insert(ShaderReviewGame)
                        .values(review_id=review_id, game_version=game)
                        .on_conflict_do_nothing()
                    )
            job = session.get(Job, identifier)
            digest = job.artifact_sha256
            paused = session.get(CollectionGate, 1).paused
        if digest:
            self.enqueue_cached(digest, observation.size, kind="jar", public=True)
        result = dict(key=key, job=self.get_job(identifier), paused=paused)
        if digest:
            facts = self.get_scan(digest, self.parser_version)
            if facts and len(json.dumps(facts).encode()) <= 2 * 1024 * 1024:
                result["facts"] = facts
        return result

    def public_release_snapshots(self, identities: list[dict[str, str]]) -> dict[str, Any]:
        """Return durable observations and only explicitly public, hash-verified facts."""
        if not 1 <= len(identities) <= 100:
            raise CollectionError("invalid_release_batch")
        items = []
        budget = 8 * 1024 * 1024
        with Session(self.engine) as session:
            for identity in identities:
                rows = session.scalars(
                    select(PublicReleaseSnapshot)
                    .where(
                        PublicReleaseSnapshot.provider == identity["provider"],
                        PublicReleaseSnapshot.project_id == identity["project_id"],
                        PublicReleaseSnapshot.release_id == identity["release_id"],
                    )
                    .order_by(PublicReleaseSnapshot.last_seen.desc(), PublicReleaseSnapshot.id)
                    .limit(101)
                ).all()
                snapshots = []
                truncated = len(rows) > 100
                for row in rows[:100]:
                    size = len(json.dumps(row.payload, ensure_ascii=False).encode("utf-8")) + 512
                    if size > budget:
                        truncated = True
                        break
                    budget -= size
                    digest = session.scalar(
                        select(ArtifactHash.artifact_sha256)
                        .join(CachedArtifact, CachedArtifact.sha256 == ArtifactHash.artifact_sha256)
                        .where(
                            ArtifactHash.algorithm == row.algorithm,
                            ArtifactHash.digest == row.digest,
                            CachedArtifact.public.is_(True),
                        )
                    )
                    if row.algorithm == "sha256" and not digest:
                        digest = session.scalar(
                            select(CachedArtifact.sha256).where(
                                CachedArtifact.sha256 == row.digest,
                                CachedArtifact.public.is_(True),
                            )
                        )
                    if row.algorithm == "sha1" and not digest:
                        observations = session.execute(
                            select(ReleaseObservation, Job.artifact_sha256)
                            .join(Job, Job.id == ReleaseObservation.job_id)
                            .join(CachedArtifact, CachedArtifact.sha256 == Job.artifact_sha256)
                            .where(
                                ReleaseObservation.provider == row.provider,
                                ReleaseObservation.release_id == row.release_id,
                                Job.state == "succeeded",
                                CachedArtifact.public.is_(True),
                            )
                        ).all()
                        for observation, verified in observations:
                            recorded = observation.payload
                            if (
                                recorded.get("project_id") == row.project_id
                                and recorded.get("algorithm") == row.algorithm
                                and recorded.get("digest", "").lower() == row.digest
                            ):
                                digest = verified
                                break
                    snapshots.append(
                        dict(
                            revision=row.id,
                            observation=row.payload,
                            observed_at=row.last_seen.isoformat(),
                            verified_sha256=digest,
                        )
                    )
                items.append(
                    dict(
                        identity=identity,
                        state="observed" if rows else "missing",
                        snapshots=snapshots,
                        truncated=truncated,
                    )
                )
        return dict(schema="bugg-dependency-releases-v1", items=items)

    def shader_reviews(self, game_version: str | None, limit: int) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            query = select(ShaderReview).order_by(ShaderReview.created_at.desc(), ShaderReview.id)
            if game_version:
                query = query.join(ShaderReviewGame).where(
                    ShaderReviewGame.game_version == game_version
                )
            rows = session.scalars(query.limit(limit)).all()
            result = []
            for row in rows:
                decisions = session.scalars(
                    select(ShaderReviewDecision)
                    .where(ShaderReviewDecision.review_id == row.id)
                    .order_by(ShaderReviewDecision.created_at, ShaderReviewDecision.id)
                ).all()
                result.append(
                    dict(
                        id=row.id,
                        state=row.state,
                        observation=row.payload,
                        observation_key=row.observation_key,
                        created_at=row.created_at.isoformat(),
                        decisions=[
                            dict(
                                reviewer=d.reviewer,
                                conclusion=d.conclusion,
                                note=d.note,
                                created_at=d.created_at.isoformat(),
                            )
                            for d in decisions
                        ],
                    )
                )
            return result

    def decide_shader_review(
        self, review_id: str, reviewer: str, conclusion: str, note: str
    ) -> bool:
        with Session(self.engine) as session, session.begin():
            row = session.scalar(
                select(ShaderReview).where(ShaderReview.id == review_id).with_for_update()
            )
            if row is None:
                return False
            now = utcnow()
            session.add(
                ShaderReviewDecision(
                    id=str(uuid4()),
                    review_id=review_id,
                    reviewer=reviewer,
                    conclusion=conclusion,
                    note=note,
                    created_at=now,
                )
            )
            row.state = "pending" if conclusion == "needs_investigation" else "reviewed"
            row.updated_at = now
            return True

    def finish(
        self,
        identifier: str,
        token: str,
        *,
        result: ScanResult | None = None,
        error: str | None = None,
        release: bool = False,
        cache_hit: bool = False,
    ) -> bool:
        now = utcnow()
        with Session(self.engine) as session, session.begin():
            job = session.scalar(
                select(Job)
                .where(
                    Job.id == identifier,
                    Job.claim_token == token,
                    Job.state == "running",
                    Job.lease_until > now,
                )
                .with_for_update()
            )
            if not job:
                return False
            if job.cancel_requested:
                job.state = "cancelled"
            elif release:
                job.state = "queued"
                job.attempts = max(0, job.attempts - 1)
            elif result is not None:
                if result.parser_version != job.parser_version:
                    raise CollectionError("parser_version_mismatch")
                self._save_scan(session, result)
                if job.request.get("expected_hash") and job.request["hash_algorithm"] == "sha512":
                    session.execute(
                        self.insert(ArtifactHash)
                        .values(
                            algorithm="sha512",
                            digest=job.request["expected_hash"],
                            artifact_sha256=result.artifact_sha256,
                        )
                        .on_conflict_do_nothing()
                    )
                job.artifact_sha256, job.state = result.artifact_sha256, "succeeded"
                job.cache_hit = cache_hit
                if session.scalar(
                    select(ReleaseObservation.key)
                    .where(ReleaseObservation.job_id == identifier)
                    .limit(1)
                ):
                    session.execute(
                        self.insert(CachedArtifact)
                        .values(
                            sha256=result.artifact_sha256,
                            kind="jar",
                            public=True,
                            job_id=identifier,
                            updated_at=now,
                        )
                        .on_conflict_do_update(
                            index_elements=[CachedArtifact.sha256],
                            set_={"public": True, "job_id": identifier, "updated_at": now},
                        )
                    )
            else:
                job.state, job.error = "failed", error or "collection_failed"
            job.updated_at, job.claim_token, job.lease_until = now, None, None
            return True

    def _save_scan(self, session: Session, result: ScanResult) -> None:
        """Both dialects enforce uniqueness atomically, including concurrent identical downloads."""
        session.execute(
            self.insert(Artifact)
            .values(
                sha256=result.artifact_sha256,
                size=result.size,
                created_at=utcnow(),
            )
            .on_conflict_do_nothing()
        )
        self._stage_archive(session, result)
        session.execute(
            self.insert(Scan)
            .values(
                id=str(uuid4()),
                artifact_sha256=result.artifact_sha256,
                parser_version=result.parser_version,
                status=result.status,
                result=result.model_dump(mode="json"),
                created_at=utcnow(),
            )
            .on_conflict_do_nothing()
        )

    def save_local_scan(self, result: ScanResult) -> None:
        with Session(self.engine) as session, session.begin():
            self._save_scan(session, result)

    def get_scan(self, digest: str, parser_version: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            scan = session.scalar(
                select(Scan).where(
                    Scan.artifact_sha256 == digest,
                    Scan.parser_version == parser_version,
                )
            )
            return scan.result if scan else None

    def enqueue_cached(self, digest: str, size: int, *, kind: str, public: bool) -> dict[str, Any]:
        """After verified CAS upload, register visibility and reuse the original leased queue."""
        if (
            not re.fullmatch(r"[a-f0-9]{64}", digest)
            or kind not in ("jar", "archive", "other")
            or size < 0
        ):
            raise CollectionError("invalid_cached_artifact")
        with Session(self.engine) as session, session.begin():
            self._gate(session)
            existing = session.get(Artifact, digest)
            if existing is not None and existing.size != size:
                raise CollectionError("cached_artifact_size_mismatch")
            session.execute(
                self.insert(Artifact)
                .values(sha256=digest, size=size, created_at=utcnow())
                .on_conflict_do_nothing()
            )
            item = session.get(CachedArtifact, digest)
            if item is None:
                session.add(
                    CachedArtifact(sha256=digest, kind=kind, public=public, updated_at=utcnow())
                )
            else:
                item.public = item.public or public
                if kind != "other" and (item.kind == "other" or kind == "jar"):
                    item.kind = kind
                item.updated_at = utcnow()
                kind = item.kind
        if kind != "other":
            identifier = self.enqueue(
                CollectRequest(
                    url=f"cache://sha256/{digest}",
                    expected_hash=digest,
                    source_provider="cache",
                    source_file_id=digest,
                    purpose="warmup",
                )
            )
            with Session(self.engine) as session, session.begin():
                session.execute(
                    update(CachedArtifact)
                    .where(CachedArtifact.sha256 == digest)
                    .values(job_id=identifier, updated_at=utcnow())
                )
        return self.cached_state(digest)

    def _cached_states(self, digests: list[str], *, public_only: bool) -> dict[str, dict[str, Any]]:
        """Bounded scalar lookup first; large JSON snapshots are not loaded speculatively."""
        query = (
            select(
                CachedArtifact.sha256,
                CachedArtifact.kind,
                CachedArtifact.public,
                Artifact.size,
                Scan.status,
                func.length(cast(Scan.result, Text)).label("facts_bytes"),
                Job.state.label("job_state"),
                Job.error,
            )
            .join(Artifact, Artifact.sha256 == CachedArtifact.sha256)
            .outerjoin(
                Scan,
                and_(
                    Scan.artifact_sha256 == CachedArtifact.sha256,
                    Scan.parser_version == self.parser_version,
                ),
            )
            .outerjoin(
                Job,
                and_(Job.id == CachedArtifact.job_id, Job.parser_version == self.parser_version),
            )
            .where(CachedArtifact.sha256.in_(digests))
        )
        if public_only:
            query = query.where(CachedArtifact.public.is_(True))
        with Session(self.engine) as session:
            results = {}
            for row in session.execute(query).mappings():
                state = row["status"] or (
                    "not_applicable"
                    if row["kind"] == "other"
                    else "failed"
                    if row["job_state"] in ("failed", "cancelled")
                    else "running"
                    if row["job_state"] == "running"
                    else "pending"
                )
                results[row["sha256"]] = dict(
                    sha256=row["sha256"],
                    size=row["size"],
                    state=state,
                    parser_version=self.parser_version,
                    error=None if row["status"] else row["error"],
                    kind=row["kind"],
                    published=row["public"],
                    facts_bytes=row["facts_bytes"],
                )
            return results

    def cached_state(self, digest: str) -> dict[str, Any]:
        result = self._cached_states([digest], public_only=False).get(digest)
        if result is None:
            return dict(sha256=digest, state="missing", parser_version=self.parser_version)
        result.pop("facts_bytes", None)
        return result

    def public_scans(self, digests: list[str]) -> dict[str, Any]:
        """Only explicitly published facts. No download, parse, task or private-hash oracle."""
        if not 1 <= len(digests) <= 100:
            raise CollectionError("invalid_facts_batch")
        states = self._cached_states(digests, public_only=True)
        budget, items = 8 * 1024 * 1024, []
        for digest in digests:
            item = states.get(
                digest, dict(sha256=digest, state="missing", parser_version=self.parser_version)
            )
            length = item.pop("facts_bytes", None)
            if length is not None:
                if length > min(budget, 2 * 1024 * 1024):
                    item.update(state="unsupported", error="facts_response_budget")
                else:
                    facts = self.get_scan(digest, self.parser_version)
                    size = len(json.dumps(facts, ensure_ascii=False).encode())
                    if size > budget:
                        item.update(state="unsupported", error="facts_response_budget")
                    else:
                        item["facts"] = facts
                        budget -= size
            items.append(item)
        return dict(
            schema="bugg-dependency-facts-v1", parser_version=self.parser_version, items=items
        )

    def artifacts(self, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        """Read only bounded scalar summaries, independent of recent job history."""
        if not 1 <= limit <= 100:
            raise CollectionError("invalid_artifact_limit")
        with Session(self.engine) as session:
            query = select(
                Scan.id.label("scan_id"),
                Scan.artifact_sha256,
                Artifact.size,
                Scan.parser_version,
                Scan.status,
                Scan.created_at,
            ).join(Artifact, Artifact.sha256 == Scan.artifact_sha256)
            if cursor is not None:
                anchor = session.execute(
                    select(Scan.created_at, Scan.id).where(Scan.id == cursor)
                ).one_or_none()
                if anchor is None:
                    raise CollectionError("invalid_artifact_cursor")
                query = query.where(
                    or_(
                        Scan.created_at < anchor.created_at,
                        and_(Scan.created_at == anchor.created_at, Scan.id < anchor.id),
                    )
                )
            rows = (
                session.execute(
                    query.order_by(Scan.created_at.desc(), Scan.id.desc()).limit(limit + 1)
                )
                .mappings()
                .all()
            )
            return {
                "items": [
                    dict(row, created_at=row["created_at"].isoformat()) for row in rows[:limit]
                ],
                "next_cursor": rows[limit - 1]["scan_id"] if len(rows) > limit else None,
            }

    def _stage_archive(self, session: Session, result: ScanResult) -> None:
        if self.cloud is None:
            return
        objects = {f"blobs/{result.artifact_sha256}.jar": (result.artifact_sha256, result.size)}
        objects.update({f"evidence/{e.object_key}": (e.sha256, e.size) for e in result.evidence})
        now = utcnow()
        rows = [
            dict(
                storage_id=self.cloud.storage_id,
                object_key=f"{self.cloud.object_prefix}/{local}",
                local_key=local,
                sha256=digest,
                size=size,
                state="queued",
                attempts=0,
                capacity_reserved=False,
                created_at=now,
                updated_at=now,
            )
            for local, (digest, size) in objects.items()
        ]
        for offset in range(0, len(rows), 100):
            session.execute(
                self.insert(ArchiveObject)
                .values(rows[offset : offset + 100])
                .on_conflict_do_nothing()
            )

    def backfill_archives(self, limit: int = 100) -> int:
        if self.cloud is None:
            raise CollectionError("cloud_not_configured")
        with Session(self.engine) as session, session.begin():
            rows = session.scalars(
                select(Scan)
                .where(
                    Scan.parser_version == self.parser_version,
                    ~exists().where(
                        ArchiveObject.storage_id == self.cloud.storage_id,
                        ArchiveObject.object_key
                        == (
                            literal(self.cloud.object_prefix + "/blobs/")
                            + Scan.artifact_sha256
                            + literal(".jar")
                        ),
                    ),
                )
                .order_by(Scan.created_at)
                .limit(limit)
            ).all()
            for row in rows:
                self._stage_archive(session, ScanResult.model_validate(row.result))
            return len(rows)

    def claim_archive(self) -> dict[str, Any] | None:
        if self.cloud is None:
            raise CollectionError("cloud_not_configured")
        now = utcnow()
        storage_id, day = self.cloud.storage_id, now.date().isoformat()
        eligible = or_(
            ArchiveObject.state == "queued",
            and_(ArchiveObject.state == "running", ArchiveObject.lease_until < now),
        )
        with Session(self.engine) as session, session.begin():
            self._gate(session)
            session.execute(
                update(ArchiveObject)
                .where(
                    ArchiveObject.storage_id == storage_id, eligible, ArchiveObject.attempts >= 3
                )
                .values(
                    state="failed", error="lease_retry_limit", claim_token=None, lease_until=None
                )
            )
            running = session.scalars(
                select(ArchiveObject.object_key)
                .where(
                    ArchiveObject.storage_id == storage_id,
                    ArchiveObject.state == "running",
                    ArchiveObject.lease_until > now,
                )
                .limit(self.settings.max_running_jobs)
            ).all()
            if len(running) >= self.settings.max_running_jobs:
                return None
            item = session.scalar(
                select(ArchiveObject)
                .where(
                    ArchiveObject.storage_id == storage_id,
                    eligible,
                    ArchiveObject.attempts < 3,
                )
                .order_by(ArchiveObject.created_at, ArchiveObject.object_key)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if item is None:
                return None
            session.execute(
                self.insert(ArchiveSpace)
                .values(
                    storage_id=storage_id,
                    reserved_bytes=0,
                )
                .on_conflict_do_nothing()
            )
            if not item.capacity_reserved:
                changed = session.execute(
                    update(ArchiveSpace)
                    .where(
                        ArchiveSpace.storage_id == storage_id,
                        ArchiveSpace.reserved_bytes + item.size <= self.cloud.capacity_bytes,
                    )
                    .values(reserved_bytes=ArchiveSpace.reserved_bytes + item.size)
                )
                if changed.rowcount != 1:
                    item.state, item.error = "failed", "cloud_capacity_limit"
                    return None
                item.capacity_reserved = True
            session.execute(
                self.insert(UploadBudget)
                .values(
                    storage_id=storage_id,
                    day=day,
                    reserved_bytes=0,
                )
                .on_conflict_do_nothing()
            )
            changed = session.execute(
                update(UploadBudget)
                .where(
                    UploadBudget.storage_id == storage_id,
                    UploadBudget.day == day,
                    UploadBudget.reserved_bytes + item.size * 3
                    <= self.cloud.daily_upload_gib * 1024**3,
                )
                .values(reserved_bytes=UploadBudget.reserved_bytes + item.size * 3)
            )
            if changed.rowcount != 1:
                item.state, item.error = "failed", "cloud_daily_upload_budget_exhausted"
                return None
            item.state, item.claim_token = "running", str(uuid4())
            item.attempts += 1
            item.updated_at, item.lease_until = now, now + timedelta(seconds=self.lease_seconds)
            return dict(
                key=item.object_key,
                local_key=item.local_key,
                sha256=item.sha256,
                size=item.size,
                token=item.claim_token,
            )

    def pulse_archive(self, key: str, token: str) -> None:
        now = utcnow()
        with Session(self.engine) as session, session.begin():
            changed = session.execute(
                update(ArchiveObject)
                .where(
                    ArchiveObject.storage_id == self.cloud.storage_id,
                    ArchiveObject.object_key == key,
                    ArchiveObject.claim_token == token,
                    ArchiveObject.state == "running",
                    ArchiveObject.lease_until > now,
                )
                .values(updated_at=now, lease_until=now + timedelta(seconds=self.lease_seconds))
            )
            if changed.rowcount != 1:
                raise Cancelled("archive_lease_lost")

    def finish_archive(
        self, key: str, token: str, *, error: str | None = None, release: bool = False
    ) -> bool:
        now = utcnow()
        with Session(self.engine) as session, session.begin():
            changed = session.execute(
                update(ArchiveObject)
                .where(
                    ArchiveObject.storage_id == self.cloud.storage_id,
                    ArchiveObject.object_key == key,
                    ArchiveObject.claim_token == token,
                    ArchiveObject.state == "running",
                    ArchiveObject.lease_until > now,
                )
                .values(
                    state="queued" if release else "failed" if error else "succeeded",
                    error=error,
                    updated_at=now,
                    lease_until=None,
                    claim_token=None,
                )
            )
            return changed.rowcount == 1

    def retry_archives(self, limit: int = 100) -> int:
        if self.cloud is None:
            raise CollectionError("cloud_not_configured")
        with Session(self.engine) as session, session.begin():
            self._gate(session)
            rows = session.scalars(
                select(ArchiveObject)
                .where(
                    ArchiveObject.storage_id == self.cloud.storage_id,
                    ArchiveObject.state == "failed",
                )
                .order_by(ArchiveObject.updated_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).all()
            for row in rows:
                row.state, row.attempts, row.error, row.updated_at = "queued", 0, None, utcnow()
            return len(rows)

    def status(self) -> dict[str, Any]:
        day = utcnow().date().isoformat()
        with Session(self.engine) as session:
            active = dict(
                session.execute(
                    select(Job.state, func.count())
                    .where(
                        Job.state.in_(("queued", "running")),
                    )
                    .group_by(Job.state)
                ).all()
            )
            download = session.get(DownloadBudget, day)
            cloud: dict[str, Any] = {"configured": self.cloud is not None}
            if self.cloud is not None:
                space = session.get(ArchiveSpace, self.cloud.storage_id)
                upload = session.get(UploadBudget, (self.cloud.storage_id, day))
                cloud.update(
                    provider=self.cloud.provider,
                    profile_id=self.cloud.id,
                    states=dict(
                        session.execute(
                            select(ArchiveObject.state, func.count())
                            .where(
                                ArchiveObject.storage_id == self.cloud.storage_id,
                            )
                            .group_by(ArchiveObject.state)
                        ).all()
                    ),
                    reserved_capacity_bytes=space.reserved_bytes if space else 0,
                    capacity_bytes=self.cloud.capacity_bytes,
                    reserved_upload_bytes=upload.reserved_bytes if upload else 0,
                    daily_upload_bytes=self.cloud.daily_upload_gib * 1024**3,
                )
            return dict(
                budget_day_utc=day,
                active=active,
                max_pending_jobs=self.settings.max_pending_jobs,
                max_running_jobs=self.settings.max_running_jobs,
                reserved_download_bytes=download.reserved_bytes if download else 0,
                daily_download_bytes=self.settings.daily_download_bytes,
                min_free_disk_bytes=self.settings.min_free_disk_bytes,
                cloud=cloud,
            )
