"""Download/import -> local restricted decompression -> immutable declared-fact snapshot."""

import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .archive import METADATA, ArchiveReader, ExtractionBudget
from .config import Settings
from .contracts import PARSER_VERSION, Cancelled, CollectionError, CollectRequest, Issue, ScanResult
from .parsers import (
    decode_document,
    manifest_attributes,
    nested_references,
    parse_fabric,
    parse_toml,
)
from .storage import ArtifactStore, Checkpoint

ScanLookup = Callable[[str, str], dict[str, Any] | None]


class Collector:
    def __init__(self, settings: Settings, store: ArtifactStore | None = None):
        self.settings = settings
        self.store = store or ArtifactStore(settings)

    def collect(
        self,
        request: CollectRequest,
        checkpoint: Checkpoint,
        *,
        known_digest: str | None = None,
        scan_lookup: ScanLookup | None = None,
        before_download: Checkpoint | None = None,
    ) -> ScanResult:
        path = self.store.cached_blob(request, known_digest, checkpoint)
        if path is not None:
            digest = known_digest
        else:
            path, digest = self.store.download(request, checkpoint, before_download=before_download)
        return self.scan(path, digest, checkpoint, scan_lookup=scan_lookup)

    def import_local(
        self, path: Path, checkpoint: Checkpoint, *, scan_lookup: ScanLookup | None = None
    ) -> ScanResult:
        local, digest = self.store.import_local(path, checkpoint)
        return self.scan(local, digest, checkpoint, scan_lookup=scan_lookup)

    def scan(
        self,
        path: Path,
        digest: str,
        checkpoint: Checkpoint,
        *,
        scan_lookup: ScanLookup | None = None,
    ) -> ScanResult:
        checkpoint()
        cached = scan_lookup(digest, PARSER_VERSION) if scan_lookup else None
        if cached is not None:
            result = ScanResult.model_validate(cached)
            if (
                result.artifact_sha256 != digest
                or result.parser_version != PARSER_VERSION
                or result.size != path.stat().st_size
            ):
                raise CollectionError("scan_cache_mismatch")
            return result
        result = ScanResult(artifact_sha256=digest, size=path.stat().st_size, status="unsupported")
        budget = ExtractionBudget(self.settings)
        self._visit(path, digest, "root", 0, budget, checkpoint, result)
        if result.mods:
            result.status = "partial" if result.issues else "complete_declared"
        elif result.issues:
            result.status = "partial"
        return result

    def _visit(
        self,
        path: Path,
        root_digest: str,
        container: str,
        depth: int,
        budget: ExtractionBudget,
        checkpoint: Checkpoint,
        result: ScanResult,
    ) -> None:
        checkpoint()
        budget.jars += 1
        if depth > self.settings.max_depth or budget.jars > self.settings.max_nested_jars:
            raise CollectionError("nested_jar_limit")
        try:
            with ArchiveReader(
                path,
                self.store.root / "evidence" / root_digest,
                container,
                budget,
                checkpoint,
            ) as archive:
                documents = {}
                for name in METADATA:
                    checkpoint()
                    extracted = archive.extract(name)
                    if extracted:
                        local, evidence = extracted
                        result.evidence.append(evidence)
                        documents[name] = local.read_bytes()
                implementation_version = None
                container_type = None
                first_mod = len(result.mods)
                if raw_manifest := documents.get("META-INF/MANIFEST.MF"):
                    try:
                        attributes = manifest_attributes(raw_manifest)
                        implementation_version = attributes.get("implementation-version")
                        container_type = attributes.get("fmlmodtype")
                    except (UnicodeError, CollectionError) as exc:
                        result.issues.append(
                            Issue(
                                code="invalid_manifest", path=container, detail=type(exc).__name__
                            )
                        )
                references = []
                for name, raw in documents.items():
                    if name == "META-INF/MANIFEST.MF":
                        continue
                    if name in ("quilt.mod.json", "mcmod.info"):
                        result.issues.append(
                            Issue(code="unsupported_metadata_format", path=f"{container}!/{name}")
                        )
                        continue
                    try:
                        document = decode_document(name, raw)
                        if name == "fabric.mod.json":
                            result.mods.extend(parse_fabric(document, container, name))
                            references.extend(nested_references(name, document))
                        elif name.endswith("mods.toml"):
                            mods, issues = parse_toml(
                                document, container, name, implementation_version
                            )
                            result.mods.extend(mods)
                            result.issues.extend(issues)
                        elif name == "META-INF/jarjar/metadata.json":
                            references.extend(nested_references(name, document))
                    except (
                        ValueError,
                        UnicodeError,
                        TypeError,
                        CollectionError,
                        RecursionError,
                    ) as exc:
                        result.issues.append(
                            Issue(
                                code=str(exc)
                                if isinstance(exc, CollectionError)
                                else "malformed_metadata",
                                path=f"{container}!/{name}",
                                detail=type(exc).__name__,
                            )
                        )
                for mod in result.mods[first_mod:]:
                    mod.container_type = container_type
                for name in dict.fromkeys(references):
                    checkpoint()
                    extracted = archive.extract(name, nested=True)
                    if extracted is None:
                        result.issues.append(
                            Issue(code="missing_nested_jar", path=f"{container}!/{name}")
                        )
                        continue
                    nested_path, evidence = extracted
                    result.evidence.append(evidence)
                    self._visit(
                        nested_path,
                        root_digest,
                        f"{container}!/{name}",
                        depth + 1,
                        budget,
                        checkpoint,
                        result,
                    )
        except Cancelled:
            raise
        except OSError as exc:
            raise CollectionError("evidence_io_error") from exc
        except (
            zipfile.BadZipFile,
            zlib.error,
            RuntimeError,
            NotImplementedError,
            CollectionError,
        ) as exc:
            if container == "root":
                if isinstance(exc, CollectionError):
                    raise
                raise CollectionError("invalid_jar_archive") from exc
            result.issues.append(
                Issue(
                    code=str(exc) if isinstance(exc, CollectionError) else "invalid_nested_archive",
                    path=container,
                    detail=type(exc).__name__,
                )
            )
