"""Restricted decompression to local evidence; never extractall or execute archives."""

import hashlib
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .config import Settings
from .contracts import CollectionError, Evidence
from .storage import Checkpoint, publish_file

METADATA = (
    "fabric.mod.json",
    "META-INF/mods.toml",
    "META-INF/neoforge.mods.toml",
    "META-INF/MANIFEST.MF",
    "META-INF/jarjar/metadata.json",
    "quilt.mod.json",
    "mcmod.info",
)


class ExtractionBudget:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bytes_used = 0
        self.jars = 0

    def consume(self, size: int) -> None:
        self.bytes_used += size
        if self.bytes_used > self.settings.max_extracted_bytes:
            raise CollectionError("extraction_budget_exceeded")


class ArchiveReader:
    def __init__(
        self,
        path: Path,
        evidence_root: Path,
        container: str,
        budget: ExtractionBudget,
        checkpoint: Checkpoint,
    ):
        self.path = path
        self.evidence_root = evidence_root
        self.container = container
        self.budget = budget
        self.checkpoint = checkpoint
        self.archive: zipfile.ZipFile | None = None

    def __enter__(self) -> "ArchiveReader":
        self.archive = zipfile.ZipFile(self.path)
        entries = self.archive.infolist()
        if len(entries) > self.budget.settings.max_entries:
            self.archive.close()
            raise CollectionError("zip_entry_limit")
        names = [entry.filename for entry in entries]
        if len(set(names)) != len(names):
            self.archive.close()
            raise CollectionError("duplicate_zip_entry")
        return self

    def __exit__(self, *args: object) -> None:
        if self.archive:
            self.archive.close()

    def extract(self, name: str, *, nested: bool = False) -> tuple[Path, Evidence] | None:
        """Decompress selected member, checking CRC and shared byte budget, into fixed CAS path."""
        normalized = PurePosixPath(name)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or ":" in name
            or any(part in ("", ".", "..") for part in name.split("/"))
            or normalized.is_absolute()
        ):
            raise CollectionError("unsafe_archive_path")
        assert self.archive is not None
        try:
            entry = self.archive.getinfo(name)
        except KeyError:
            return None
        if entry.flag_bits & 1 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
            raise CollectionError("encrypted_or_symlink_member")
        limit = (
            self.budget.settings.max_extracted_bytes
            if nested
            else self.budget.settings.max_metadata_bytes
        )
        if entry.file_size > limit:
            raise CollectionError("member_size_limit")
        if entry.file_size > max(1, entry.compress_size) * 300:
            raise CollectionError("compression_ratio_limit")
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.evidence_root, suffix=".part")
        temporary_path = Path(temporary)
        digest = hashlib.sha256()
        count = 0
        try:
            with os.fdopen(descriptor, "wb") as target, self.archive.open(entry) as source:
                while chunk := source.read(64 * 1024):
                    self.checkpoint()
                    count += len(chunk)
                    self.budget.consume(len(chunk))
                    if count > limit:
                        raise CollectionError("member_size_limit")
                    target.write(chunk)
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            if count != entry.file_size:
                raise CollectionError("member_size_mismatch")
            # Hostile member paths never become filesystem paths. Each payload is content-addressed.
            destination = self.evidence_root / digest.hexdigest()
            publish_file(temporary_path, destination)
            return destination, Evidence(
                container_path=self.container,
                member_path=name,
                sha256=digest.hexdigest(),
                size=count,
                object_key=f"{self.evidence_root.name}/{digest.hexdigest()}",
            )
        finally:
            temporary_path.unlink(missing_ok=True)
