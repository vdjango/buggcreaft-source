"""Recoverable index execution; all inference is outside database transactions."""

import logging
import signal
import threading

from sqlalchemy.exc import SQLAlchemyError

from .clients import SearchClients, SearchUnavailable
from .repository import SearchRepository
from .text import chunks

LOG = logging.getLogger(__name__)


class SearchWorker:
    def __init__(self, repository: SearchRepository, clients: SearchClients):
        self.repository, self.clients = repository, clients
        self.stopping = threading.Event()

    def stop(self, *args):
        self.stopping.set()

    def ingest(self):
        claim = self.repository.feed_claim()
        if not claim:
            return False
        token, state = claim
        try:
            batch = self.clients.call(
                self.clients.settings.source_url,
                f"/_internal/search/changes?after={state['cursor']}&limit=8",
            )
            return self.repository.feed_commit(token, batch)
        finally:
            self.repository.feed_release(token)

    def run_once(self):
        self.ingest()
        job = self.repository.claim()
        if not job:
            return False
        try:
            payload = job["payload"]
            items, coverage = (
                chunks(payload) if payload["visible"] else ([], {"body_available": False})
            )
            semantic_error = None
            if items and self.clients.settings.embedding_url:
                try:
                    for start in range(0, len(items), 16):
                        if self.stopping.is_set():
                            raise SearchUnavailable("search_worker_stopping")
                        self.repository.pulse(job)
                        batch = items[start : start + 16]
                        vectors = self.clients.embed([item["text"] for item in batch])
                        for item, vector in zip(batch, vectors, strict=True):
                            item["embedding"] = vector
                except SearchUnavailable as exc:
                    semantic_error = str(exc)
                    for item in items:
                        item.pop("embedding", None)
            current = self.clients.validate([job["project_id"]], epoch=self.repository.epoch()).get(
                job["project_id"]
            )
            if (
                not current
                and payload["visible"]
                or current
                and (
                    current["revision"] != job["revision"]
                    or current["visible"] != payload["visible"]
                )
            ):
                raise SearchUnavailable("source_revision_superseded")
            self.repository.publish(job, items, coverage, semantic_error=semantic_error)
        except (SearchUnavailable, ValueError, KeyError) as exc:
            self.repository.fail(job, str(exc))
        return True

    def run(self, *, once=False):
        self.repository.ready()
        previous = {s: signal.signal(s, self.stop) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            while not self.stopping.is_set():
                try:
                    worked = self.run_once()
                except (SearchUnavailable, SQLAlchemyError) as exc:
                    LOG.warning("Search synchronization paused: %s", exc)
                    worked = False
                if once:
                    return
                self.stopping.wait(0.1 if worked else 2)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
