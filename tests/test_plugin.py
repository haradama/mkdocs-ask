from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from mkdocs.commands.build import build
from mkdocs.config import load_config
from mkdocs.utils.yaml import yaml_load

import mkdocs_ask.plugin as plugin_module

EXAMPLE = Path(__file__).parent.parent / "example"


def deep_merge(base: dict, overrides: dict) -> dict:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def run_build(tmp_path, ask_overrides):
    """Build the example site with `ask_overrides` merged into its plugin config."""
    # mkdocs.yml carries YAML tags (toc slugify), so use MkDocs' own loader.
    with open(EXAMPLE / "mkdocs.yml", encoding="utf-8") as fh:
        raw = yaml_load(fh)
    plugins = raw["plugins"]
    for entry in plugins:
        if isinstance(entry, dict) and "ask" in entry:
            deep_merge(entry["ask"], ask_overrides)
    cfg = load_config(
        config_file=str(EXAMPLE / "mkdocs.yml"),
        site_dir=str(tmp_path / "site"),
        plugins=plugins,
    )
    build(cfg)
    return tmp_path / "site"


def test_build_keyword_only(tmp_path):
    site = run_build(tmp_path, {"embedding": {"backend": "none"}})
    out = site / "assets" / "ask"
    for name in (
        "ask.js",
        "worker.js",
        "search-core.js",
        "generator.js",
        "searchbox.js",
        "ask.css",
        "manifest.json",
        "chunks.json",
    ):
        assert (out / name).exists(), name
    assert not (out / "vectors.bin").exists()

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["vectors"] is None
    assert manifest["model"] is None
    assert manifest["language"] == "en"
    assert manifest["chunks"]["count"] > 3
    assert manifest["search"]["hybrid"] is True
    assert manifest["ui"]["title"]

    assert manifest["search"]["fusion"] == "rrf"
    # The theme's search field is the only entry point: no launcher, no position.
    assert manifest["search_box"]["mode"] == "augment"
    assert "position" not in manifest["ui"]
    assert "launcher_label" not in manifest["ui"]
    assert manifest["ui"]["preload"] == "focus"

    chunks = json.loads((out / "chunks.json").read_text())
    urls = {c["url"] for c in chunks}
    assert any(u.startswith("guide/auth/#") for u in urls)
    # Both exclude patterns from the example's mkdocs.yml, a plain name and a glob.
    assert not any(u.startswith("changelog") for u in urls)
    assert not any(u.startswith("api/generated") for u in urls)
    # Deep links: every chunk on a page with headings carries an anchor.
    auth = [c for c in chunks if c["page_url"] == "guide/auth/"]
    assert len(auth) > 5
    assert all(c["url"].startswith("guide/auth/#") for c in auth)
    assert ["Authentication", "API keys", "Rotating a key"] in [c["breadcrumbs"] for c in auth]

    html = (site / "index.html").read_text()
    assert 'type="module"' in html and "assets/ask/ask.js" in html
    assert "assets/ask/ask.css" in html


class FakeEmbedder:
    backend = "fake"
    model_id = "fake:v1"

    def embed(self, texts):
        v = np.zeros((len(texts), 16), dtype=np.float32)
        for i, t in enumerate(texts):
            v[i, sum(map(ord, t)) % 16] = 1.0
        return v


def test_build_with_vectors(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module, "create_embedder", lambda *a, **k: FakeEmbedder())
    site = run_build(
        tmp_path,
        {"embedding": {"backend": "auto", "cache_dir": str(tmp_path / "cache")}},
    )
    out = site / "assets" / "ask"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["model"]["onnx_id"] == "Xenova/multilingual-e5-small"
    assert manifest["model"]["query_prefix"] == "query: "
    assert manifest["model"]["dim"] == 16
    assert manifest["vectors"]["dtype"] == "int8"
    n = manifest["chunks"]["count"]
    assert (out / "vectors.bin").stat().st_size == n * 16
    assert (tmp_path / "cache" / "embeddings" / "fake_v1.npz").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"search": {"top_k": 0}},
        {"search": {"keyword_weight": 0, "semantic_weight": 0}},
        {"search": {"rrf_k": 0}},
        {"chunking": {"max_chars": 200, "overlap": 200}},
        {"search_box": {"min_chars": 0}},
        {"ui": {"preload": "open"}},  # renamed to "focus" when the launcher was removed
        {"ui": {"strings": {"found": 42}}},  # every override must be a string
        {"search_box": {"max_results": 0}},
        {"search": {"snippet_chars": 0}},
        {"chunking": {"min_chars": -1}},
        {"embedding": {"batch_size": 0}},
        {"ai_answer": {"max_tokens": 0}},
        {"ai_answer": {"context_chunks": 0}},
        {"output_dir": "../escape"},
        {"output_dir": "."},
        {"output_dir": "/absolute"},
        {"output_dir": "assets/../../escape"},
    ],
)
def test_invalid_config_is_rejected(tmp_path, overrides):
    from mkdocs.exceptions import Abort, ConfigurationError, PluginError

    with pytest.raises((PluginError, ConfigurationError, Abort)):
        run_build(tmp_path, {"embedding": {"backend": "none"}, **overrides})
