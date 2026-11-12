"""Independent worker process; API enqueues only, never runs these jobs in memory."""

import signal
import time
from threading import Event

from .collector import Collector
from .config import Settings
from .contracts import Cancelled, CollectionError, CollectionPaused
from .repository import Repository


class WorkerStopping(Exception):
    pass


class Worker:
    def __init__(
        self, settings: Settings, repository: Repository, collector: Collector | None = None
    ):
        self.settings = settings
        self.repository = repository
        self.collector = collector or Collector(settings)
        self.stopping = Event()

    def stop(self, *_: object) -> None:
        self.stopping.set()

    def run_once(self) -> bool:
        claimed = self.repository.claim()
        if not claimed:
            return False
        identifier, token, request = claimed
        last_pulse = 0.0
        cache_hit = False

        def checkpoint() -> None:
            nonlocal last_pulse
            if self.stopping.is_set():
                raise WorkerStopping()
            now = time.monotonic()
            if now - last_pulse >= 1:
                self.repository.pulse(identifier, token)
                last_pulse = now

        def lookup(digest: str, version: str) -> dict | None:
            nonlocal cache_hit
            result = self.repository.get_scan(digest, version)
            cache_hit = result is not None
            return result

        def reserve() -> None:
            self.repository.reserve_download(identifier, token)

        try:
            result = self.collector.collect(
                request,
                checkpoint,
                known_digest=self.repository.known_artifact(request)
                or (request.expected_hash if request.hash_algorithm == "sha256" else None),
                scan_lookup=lookup,
                before_download=reserve,
            )
            checkpoint()
            self.repository.finish(identifier, token, result=result, cache_hit=cache_hit)
        except (WorkerStopping, CollectionPaused):
            self.repository.finish(identifier, token, release=True)
        except Cancelled:
            self.repository.finish(identifier, token, error="cancelled_or_lease_lost")
        except CollectionError as exc:
            self.repository.finish(identifier, token, error=str(exc)[:128])
        except (OSError, ValueError, RecursionError):
            self.repository.finish(identifier, token, error="collection_io_or_format_error")
        return True

    def run(self, *, once: bool = False) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        while not self.stopping.is_set():
            processed = self.run_once()
            if once:
                break
            if not processed:
                self.stopping.wait(1)
