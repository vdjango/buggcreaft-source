"""Reproducible small real-model smoke; not a production quality or latency benchmark."""

import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:18448")
    parser.add_argument("--queries", type=Path, default=Path("data/search-preview/queries.json"))
    parser.add_argument("--output", type=Path, default=Path("data/search-preview/model-smoke.json"))
    args = parser.parse_args()
    cases = json.loads(args.queries.read_text(encoding="utf-8"))
    results = []
    with httpx.Client(
        base_url=args.origin, trust_env=False, timeout=25, follow_redirects=False
    ) as client:
        capability = client.get("/v1/search/capabilities")
        capability.raise_for_status()
        if not capability.json().get("semantic"):
            raise RuntimeError("real_model_not_ready")
        for case in cases:
            for mode in ("lexical", "semantic", "hybrid"):
                started = time.perf_counter()
                response = client.get("/v1/search", params=dict(q=case["query"], mode=mode))
                response.raise_for_status()
                page = response.json()
                ids = [item["project_id"] for item in page["items"]]
                results.append(
                    dict(
                        query=case["query"],
                        expected=case["project_id"],
                        mode=mode,
                        top=ids[:5],
                        top1=bool(ids and ids[0] == case["project_id"]),
                        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                        degraded=page["degraded"],
                    )
                )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            dict(
                scope="Six local fixtures, five illustrative queries; no production benchmark.",
                capabilities=capability.json(),
                results=results,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                mode: sum(row["top1"] for row in results if row["mode"] == mode)
                for mode in ("lexical", "semantic", "hybrid")
            }
        )
    )


if __name__ == "__main__":
    main()
