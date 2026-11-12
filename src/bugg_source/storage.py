"""Only owner of bounded transfers, content hashes and local blob persistence."""

import asyncio
import hashlib
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .contracts import CollectionError, CollectRequest

Checkpoint = Callable[[], None]


def sha256_file(path: Path) -> str:
    """Hash local bytes in bounded chunks."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish_file(temporary: Path, destination: Path) -> None:
    """Publish immutable local content without replacing a blob another worker is reading."""
    try:
        os.link(temporary, destination)
    except FileExistsError:
        if sha256_file(destination) != destination.stem:
            raise CollectionError("content_store_corrupt") from None
    temporary.unlink(missing_ok=True)
    if os.name != "nt":
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class ArtifactStore:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.root = settings.data_dir.resolve()
        self.transport = transport
        for name in ("blobs", "tmp", "evidence"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def validate_url(self, url: str) -> None:
        """Allow only explicitly configured HTTPS publisher CDN hosts; no redirects by default."""
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname in self.settings.allowed_hosts
                and parsed.port in (None, 443)
                and not parsed.username
                and not parsed.password
                and not parsed.fragment
                and "\\" not in url
            )
        except ValueError:
            valid = False
        if not valid:
            raise CollectionError("download_url_not_allowed")

    def ensure_space(self) -> None:
        """Leave a small write window above the configured free-space floor."""
        if shutil.disk_usage(self.root).free < self.settings.min_free_disk_bytes + 4 * 1024 * 1024:
            raise CollectionError("disk_free_space_low")

    def cached_blob(
        self, request: CollectRequest, digest: str | None, checkpoint: Checkpoint
    ) -> Path | None:
        """Reuse a known artifact for a new parser only after hashing its actual local bytes."""
        internal = request.url.startswith("cache:")
        if internal:
            if (
                digest is None
                or request.url != f"cache://sha256/{digest}"
                or request.hash_algorithm != "sha256"
                or request.expected_hash != digest
            ):
                raise CollectionError("invalid_cached_artifact")
        else:
            self.validate_url(request.url)
        if digest is None:
            return None
        path = self.root / "blobs" / f"{digest}.jar"
        if not path.is_file():
            if internal:
                raise CollectionError("cached_artifact_missing")
            return None
        if path.stat().st_size > self.settings.max_download_bytes:
            raise CollectionError("download_size_limit")
        hashes = {name: hashlib.new(name) for name in {"sha256", request.hash_algorithm}}
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                checkpoint()
                for value in hashes.values():
                    value.update(chunk)
        if hashes["sha256"].hexdigest() != digest:
            raise CollectionError("content_store_corrupt")
        if (
            request.expected_hash
            and hashes[request.hash_algorithm].hexdigest() != request.expected_hash
        ):
            raise CollectionError("upstream_hash_mismatch")
        return path

    async def _disk(self, function: Callable, *args):
        """A cancelled upload must drain its current disk operation before closing/unlinking."""
        operation = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            await operation
            raise

    async def receive(
        self, chunks: AsyncIterator[bytes], expected_sha256: str, expected_size: int
    ) -> int:
        """Trusted internal upload only: bounded stream to CAS; no archive parsing here."""
        if not 0 < expected_size <= self.settings.max_download_bytes:
            raise CollectionError("download_size_limit")
        await asyncio.to_thread(self.ensure_space)
        descriptor, temporary = tempfile.mkstemp(suffix=".part", dir=self.root / "tmp")
        path = Path(temporary)
        digest, size = hashlib.sha256(), 0
        try:
            with os.fdopen(descriptor, "wb") as target:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.settings.max_download_bytes:
                        raise CollectionError("download_size_limit")
                    if size > expected_size:
                        raise CollectionError("cached_artifact_size_mismatch")
                    if size // (4 * 1024 * 1024) != (size - len(chunk)) // (4 * 1024 * 1024):
                        await asyncio.to_thread(self.ensure_space)
                    await self._disk(target.write, chunk)
                    digest.update(chunk)
                await self._disk(target.flush)
                await self._disk(os.fsync, target.fileno())
            if not size:
                raise CollectionError("empty_download")
            if size != expected_size:
                raise CollectionError("cached_artifact_size_mismatch")
            if digest.hexdigest() != expected_sha256:
                raise CollectionError("upstream_hash_mismatch")
            await self._disk(publish_file, path, self.root / "blobs" / f"{expected_sha256}.jar")
            return size
        finally:
            path.unlink(missing_ok=True)

    def download(
        self,
        request: CollectRequest,
        checkpoint: Checkpoint,
        *,
        before_download: Checkpoint | None = None,
    ) -> tuple[Path, str]:
        """Fetch full local JAR, validate optional publisher digest, then publish by SHA-256."""
        self.validate_url(request.url)
        self.ensure_space()
        checkpoint()
        if before_download:
            before_download()
        deadline = time.monotonic() + self.settings.download_deadline
        descriptor, temporary = tempfile.mkstemp(suffix=".part", dir=self.root / "tmp")
        path = Path(temporary)
        hashes = {name: hashlib.new(name) for name in {"sha256", request.hash_algorithm}}
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as target:
                with httpx.Client(
                    transport=self.transport,
                    timeout=httpx.Timeout(20, connect=10),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    url = request.url
                    for _ in range(4):
                        checkpoint()
                        self.validate_url(url)
                        with client.stream(
                            "GET", url, headers={"Accept-Encoding": "identity"}
                        ) as response:
                            if response.is_redirect:
                                url = str(response.next_request.url)
                                continue
                            response.raise_for_status()
                            if response.headers.get("Content-Encoding", "identity") != "identity":
                                raise CollectionError("encoded_download_rejected")
                            for chunk in response.iter_raw(64 * 1024):
                                checkpoint()
                                if time.monotonic() > deadline:
                                    raise CollectionError("download_deadline_exceeded")
                                size += len(chunk)
                                if size > self.settings.max_download_bytes:
                                    raise CollectionError("download_size_limit")
                                if size // (4 * 1024 * 1024) != (size - len(chunk)) // (
                                    4 * 1024 * 1024
                                ):
                                    self.ensure_space()
                                target.write(chunk)
                                for digest in hashes.values():
                                    digest.update(chunk)
                            break
                    else:
                        raise CollectionError("too_many_redirects")
                target.flush()
                os.fsync(target.fileno())
            if not size:
                raise CollectionError("empty_download")
            if (
                request.expected_hash
                and hashes[request.hash_algorithm].hexdigest() != request.expected_hash
            ):
                raise CollectionError("upstream_hash_mismatch")
            checkpoint()
            digest = hashes["sha256"].hexdigest()
            destination = self.root / "blobs" / f"{digest}.jar"
            publish_file(path, destination)
            return destination, digest
        except httpx.HTTPError as exc:
            raise CollectionError("download_http_error") from exc
        finally:
            path.unlink(missing_ok=True)

    def import_local(self, source: Path, checkpoint: Checkpoint) -> tuple[Path, str]:
        """CLI-only import; copies into CAS, never scans a caller's changing file in place."""
        self.ensure_space()
        descriptor, temporary = tempfile.mkstemp(suffix=".part", dir=self.root / "tmp")
        path = Path(temporary)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as target, source.open("rb") as incoming:
                while chunk := incoming.read(64 * 1024):
                    checkpoint()
                    size += len(chunk)
                    if size > self.settings.max_download_bytes:
                        raise CollectionError("download_size_limit")
                    if size // (4 * 1024 * 1024) != (size - len(chunk)) // (4 * 1024 * 1024):
                        self.ensure_space()
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            destination = self.root / "blobs" / f"{digest.hexdigest()}.jar"
            publish_file(path, destination)
            return destination, digest.hexdigest()
        finally:
            path.unlink(missing_ok=True)
