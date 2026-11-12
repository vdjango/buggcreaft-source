"""Optional, authenticated CPU ONNX service; runtime never downloads models."""

import hashlib
import hmac
import json
import os
import threading
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field


def model_manifest(directory: Path):
    names = (
        "model_optimized.onnx",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    )
    hashes = {}
    for name in names:
        with (directory / name).open("rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    revision = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return dict(
        model="BAAI/bge-small-zh-v1.5",
        revision=revision,
        files=hashes,
        runtime="fastembed-0.7.4",
        dimensions=512,
    )


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texts: list[str] = Field(min_length=1, max_length=16)
    query: bool = False
    revision: str = Field(max_length=100)


def create_embedding_app():
    from fastembed import TextEmbedding
    from tokenizers import Tokenizer

    key = os.environ.get("BUGG_EMBEDDING_TOKEN", "")
    directory = Path(os.environ["BUGG_EMBEDDING_MODEL_DIR"]).resolve()
    manifest = model_manifest(directory)
    expected = json.loads((directory / "bugg-model.json").read_text(encoding="utf-8"))
    if not key or manifest != expected:
        raise ValueError("embedding_key_or_model_manifest_invalid")
    model = TextEmbedding(
        model_name=manifest["model"],
        specific_model_path=str(directory),
        local_files_only=True,
        threads=2,
        providers=["CPUExecutionProvider"],
    )
    tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
    tokenizer.no_truncation()
    gate = threading.BoundedSemaphore(2)
    app = FastAPI(title="Bugg private embedding service", docs_url=None, redoc_url=None)

    def authorize(supplied):
        if not supplied or not hmac.compare_digest(supplied.encode(), key.encode()):
            raise HTTPException(401, "unauthorized")

    @app.get("/health")
    def health(x_embedding_key: str = Header(default="")):
        authorize(x_embedding_key)
        return dict(revision=manifest["revision"], dimensions=512, model=manifest["model"])

    @app.post("/v1/embeddings")
    def embeddings(body: EmbeddingRequest, x_embedding_key: str = Header(default="")):
        authorize(x_embedding_key)
        if body.revision != manifest["revision"]:
            raise HTTPException(409, "embedding_space_mismatch")
        if any(not value.strip() or len(value) > 1000 for value in body.texts):
            raise HTTPException(422, "embedding_input_limit")
        # Do not silently discard the end of a chunk at the model's tokenizer limit.
        if any(len(item.ids) > 510 for item in tokenizer.encode_batch(body.texts)):
            raise HTTPException(422, "embedding_token_limit")
        if not gate.acquire(blocking=False):
            raise HTTPException(429, "embedding_busy")
        try:
            method = model.query_embed if body.query else model.embed
            vectors = [vector.tolist() for vector in method(body.texts, batch_size=16)]
            return dict(revision=manifest["revision"], vectors=vectors)
        finally:
            gate.release()

    return app
