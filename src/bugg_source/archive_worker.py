"""Independent archive process reusing the collection worker's stop/run lifecycle."""

import time

from .cloud import S3Archive
from .config import Settings
from .contracts import Cancelled, CollectionError
from .repository import Repository
from .worker import Worker, WorkerStopping


class ArchiveWorker(Worker):
    def __init__(
        self, settings: Settings, repository: Repository, archive: S3Archive | None = None
    ):
        super().__init__(settings, repository)
        if repository.cloud is None:
            raise CollectionError("cloud_not_configured")
        self.archive = archive or S3Archive(settings, repository.cloud)

    def run_once(self) -> bool:
        item = self.repository.claim_archive()
        if item is None:
            return False
        last_pulse = 0.0

        def checkpoint() -> None:
            nonlocal last_pulse
            if self.stopping.is_set():
                raise WorkerStopping()
            now = time.monotonic()
            if now - last_pulse >= 1:
                self.repository.pulse_archive(item["key"], item["token"])
                last_pulse = now

        try:
            checkpoint()
            root = self.settings.data_dir.resolve()
            path = (root / item["local_key"]).resolve()
            if not path.is_relative_to(root) or item["size"] > self.settings.max_download_bytes:
                raise CollectionError("archive_local_path_or_size_invalid")
            self.archive.put_verified(path, item["key"], item["sha256"], item["size"], checkpoint)
            checkpoint()
            self.repository.finish_archive(item["key"], item["token"])
        except WorkerStopping:
            self.repository.finish_archive(item["key"], item["token"], release=True)
        except Cancelled:
            self.repository.finish_archive(item["key"], item["token"], error="archive_lease_lost")
        except CollectionError as exc:
            self.repository.finish_archive(item["key"], item["token"], error=str(exc)[:128])
        except (OSError, ValueError):
            self.repository.finish_archive(item["key"], item["token"], error="archive_io_error")
        return True
