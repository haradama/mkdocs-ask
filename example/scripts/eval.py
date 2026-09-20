#!/usr/bin/env python3
"""Measure what hybrid retrieval buys you on the built index.

Embeds each golden query with the plugin's own embedder, then hands the vectors to
`eval.mjs`, which scores them with the real browser-side tokenizer, BM25 and fusion.
Both halves therefore exercise shipped code rather than a reimplementation.

    uv run mkdocs build          # produces site/assets/ask
    uv run python scripts/eval.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

EXAMPLE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = EXAMPLE_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from mkdocs_ask.embedder import create_embedder  # noqa: E402
from mkdocs_ask.models import resolve_model  # noqa: E402


def load_queries(path: Path) -> list[dict]:
    groups = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[dict] = []
    for group in groups:
        for query in group["queries"]:
            out.append({**query, "group": group["group"]})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="site/assets/ask", help="built index directory")
    parser.add_argument("--queries", default="scripts/queries.yml")
    parser.add_argument("--top-k", type=int, default=5, help="rank cutoff counted as a hit")
    args = parser.parse_args()

    index_dir = (EXAMPLE_DIR / args.index).resolve()
    manifest_path = index_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"no index at {index_dir}. Run `uv run mkdocs build` first.", file=sys.stderr)
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    queries = load_queries(EXAMPLE_DIR / args.queries)

    vectors: dict[str, list[float]] = {}
    model = manifest.get("model")
    if model:
        spec = resolve_model(model["name"])
        embedder = create_embedder(
            spec,
            backend="auto",
            browser_dtype=model.get("browser_dtype", "q8"),
            build_dtype="auto",
            cache_dir=EXAMPLE_DIR / ".cache" / "mkdocs-ask",
        )
        if embedder is None:
            print("no embedding backend installed; scoring keyword only", file=sys.stderr)
        else:
            texts = [spec.query_prefix + q["q"] for q in queries]
            for query, vector in zip(queries, embedder.embed(texts), strict=True):
                vectors[query["q"]] = [float(x) for x in vector]
    else:
        print("index has no vectors; scoring keyword only", file=sys.stderr)

    payload = {"queries": queries, "vectors": vectors, "top_k": args.top_k}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        payload_path = fh.name

    try:
        return subprocess.call(
            ["node", str(Path(__file__).with_suffix(".mjs")), str(index_dir), payload_path]
        )
    finally:
        Path(payload_path).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
