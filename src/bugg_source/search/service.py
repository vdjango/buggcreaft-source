"""Bounded hybrid ranking and shared pagination with authoritative revalidation."""

import hashlib
import re

from .clients import SearchClients, SearchUnavailable
from .repository import SearchRepository, encoded
from .text import clean, snippet, terms


class SearchService:
    def __init__(self, repository: SearchRepository, clients: SearchClients):
        self.repository, self.clients = repository, clients

    def capabilities(self):
        if (
            not self.repository.settings.enabled
            or self.repository.engine.dialect.name != "postgresql"
        ):
            return dict(lexical=False, semantic=False, reason="search_requires_enabled_postgresql")
        state = self.repository.status()
        semantic = False
        if self.clients.settings.embedding_url:
            try:
                health = self.clients.call(
                    self.clients.settings.embedding_url, "/health", embedding=True
                )
                semantic = (
                    health["revision"] == self.clients.settings.model_revision
                    and health["dimensions"] == self.clients.settings.dimensions
                )
            except (SearchUnavailable, KeyError):
                pass
        return dict(
            lexical=True,
            semantic=semantic,
            indexed_projects=state["lexical_ready"],
            semantic_projects=state["semantic_ready"],
            catalog_watermark=state["watermark"],
            consumed_watermark=state["cursor"],
            config_revision=state["config_revision"],
        )

    def search(self, query, mode, filters, *, limit=20, cursor=None, similar=None):
        self.repository.ready()
        if len(query) > 200 or mode not in ("lexical", "semantic", "hybrid"):
            raise ValueError("invalid_search_query")
        fingerprint = hashlib.sha256(
            encoded(dict(q=query, mode=mode, filters=filters, similar=similar)).encode()
        ).hexdigest()
        offset = 0
        if cursor:
            match = re.fullmatch(r"([A-Za-z0-9_-]{43})\.(\d{1,3})", cursor)
            if not match:
                raise ValueError("invalid_cursor")
            session = self.repository.session(fingerprint, cursor=match[1])
            offset = int(match[2])
            if offset > len(session["refs"]):
                raise ValueError("invalid_cursor")
        else:
            vector = None
            effective = mode
            reason = None
            if query and mode != "lexical" or similar:
                try:
                    vector = self.clients.embed([query], query=True)[0]
                except SearchUnavailable as exc:
                    if mode == "semantic" or similar:
                        raise
                    effective, reason = "lexical", str(exc)
            routes = self.repository.candidates(query, terms(query, query=True), filters, vector)
            if mode == "semantic" and query:
                routes = {key: value for key, value in routes.items() if key == "semantic"}
            elif mode == "lexical":
                routes.pop("semantic", None)
            ranked = {}
            for route, rows in routes.items():
                for rank, row in enumerate(rows, 1):
                    if row["project_id"] == similar:
                        continue
                    value = ranked.setdefault(row["project_id"], dict(row, score=0, match_types=[]))
                    value["score"] += (5 if route == "exact" else 1) / (60 + rank)
                    value["match_types"].append(route)
                    if route in ("lexical", "semantic"):
                        value["chunk_id"] = row["chunk_id"]
            refs = sorted(ranked.values(), key=lambda r: (-r["score"], r["project_id"]))[:200]
            metadata = dict(
                requested_mode=mode,
                effective_mode=effective,
                degraded=bool(reason),
                degradation_reason=reason,
                config_revision=self.repository.config,
                candidate_window_exhausted=len(refs) == 200,
            )
            session = self.repository.session(fingerprint, refs=refs, metadata=metadata)
        refs = session["refs"]
        rows, authority, batch_end = {}, {}, offset
        epoch = self.repository.epoch()
        if offset == len(refs):
            self.clients.validate([], epoch=epoch)
        items = []
        while offset < len(refs) and len(items) < limit:
            if offset >= batch_end:
                batch_end = min(len(refs), offset + limit)
                rows = self.repository.materialize(refs[offset:batch_end])
                authority = self.clients.validate(
                    list(dict.fromkeys(row["project_id"] for row in rows.values())), epoch=epoch
                )
            ref = refs[offset]
            offset += 1
            row = rows.get(ref["chunk_id"])
            current = authority.get(ref["project_id"])
            if (
                not row
                or not current
                or not current["visible"]
                or current["revision"] != row["revision"]
                or row["revision"] != ref["revision"]
            ):
                continue
            payload = row["payload"]
            title = next(
                (
                    d["text"]
                    for d in payload["documents"]
                    if d["field"] == "title" and not d["is_translation"]
                ),
                ref["project_id"],
            )
            translated = next(
                (
                    d["text"]
                    for d in payload["documents"]
                    if d["field"] == "title"
                    and d["is_translation"]
                    and d["language"] == filters["language"]
                ),
                None,
            )
            snippets = [
                dict(
                    snippet(row["text"], "" if similar else query),
                    field=row["field"],
                    language=row["language"],
                    is_translation=row["is_translation"],
                    source_hash=row["source_hash"],
                    document_revision=row["document_revision"],
                    preview=False,
                )
            ]
            if row["field"] != "body":
                bodies = [
                    document for document in payload["documents"] if document["field"] == "body"
                ]
                bodies.sort(key=lambda document: document["language"] != filters["language"])
                if bodies:
                    body = bodies[0]
                    snippets.append(
                        dict(
                            snippet(clean(body["text"][:4000], body["format"]), ""),
                            field="body",
                            language=body["language"],
                            is_translation=body["is_translation"],
                            source_hash=body["source_hash"],
                            document_revision=body["revision"],
                            preview=True,
                        )
                    )
            items.append(
                dict(
                    project_id=ref["project_id"],
                    project_title=title,
                    translated_title=translated,
                    resource_type=payload["resource_type"],
                    resource=payload["resource"],
                    source_url=payload["source_url"],
                    match_types=ref["match_types"],
                    document_revision=payload["document_revision"],
                    dependency_scan_status="not_linked",
                    compatibility_basis="source_metadata",
                    coverage=row["coverage"],
                    snippets=snippets,
                )
            )
        return dict(
            session["metadata"],
            items=items,
            has_more=offset < len(refs),
            next_cursor=f"{session['id']}.{offset}" if offset < len(refs) else None,
        )

    def similar(self, project_id, filters, limit, cursor):
        # Use this project's actual indexed body as a query; never fabricate a description.
        with self.repository.engine.connect() as db:
            from sqlalchemy import text

            row = (
                db.execute(
                    text("""
                SELECT payload,revision FROM search_projects WHERE
                project_id=:id AND visible AND revision=indexed_revision AND
                config_revision=:config
                """),
                    dict(id=project_id, config=self.repository.config),
                )
                .mappings()
                .first()
            )
        current = self.clients.validate([project_id], epoch=self.repository.epoch()).get(project_id)
        if (
            not row
            or not current
            or not current["visible"]
            or current["revision"] != row["revision"]
        ):
            raise SearchUnavailable("project_not_searchable")
        query = next((d["text"] for d in row["payload"]["documents"] if d["field"] == "body"), "")[
            :200
        ]
        if not query:
            raise SearchUnavailable("project_body_missing")
        return self.search(
            query, "semantic", filters, limit=limit, cursor=cursor, similar=project_id
        )
