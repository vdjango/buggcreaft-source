"""Explicit operator download using Hugging Face SDK; server startup is offline."""

import argparse
import json
from pathlib import Path

import httpx

from bugg_source.search.inference import model_manifest


def main():
    from huggingface_hub import HfApi, set_client_factory, snapshot_download

    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("data/models/bge-small-zh-v1.5"))
    parser.add_argument("--endpoint", default="https://huggingface.co")
    parser.add_argument("--revision", required=True, help="Explicit immutable upstream commit SHA")
    args = parser.parse_args()
    if len(args.revision) != 40 or any(c not in "0123456789abcdef" for c in args.revision):
        parser.error("revision must be a full commit SHA")
    set_client_factory(lambda: httpx.Client(trust_env=False, follow_redirects=True, timeout=60))
    info = HfApi(endpoint=args.endpoint).model_info(
        "Qdrant/bge-small-zh-v1.5", revision=args.revision
    )
    if info.sha != args.revision:
        raise ValueError("upstream_revision_mismatch")
    names = [
        "model_optimized.onnx",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    ]
    snapshot_download(
        repo_id="Qdrant/bge-small-zh-v1.5",
        revision=args.revision,
        endpoint=args.endpoint,
        allow_patterns=names,
        local_dir=args.directory,
        max_workers=2,
    )
    manifest = model_manifest(args.directory)
    (args.directory / "bugg-model.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(dict(model_dir=str(args.directory.resolve()), **manifest)))


if __name__ == "__main__":
    main()
