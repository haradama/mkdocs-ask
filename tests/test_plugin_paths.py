"""Plugin behaviour on the paths a normal build never takes.

Most of these decide what the site ends up telling the browser, so getting them wrong is
invisible until someone loads the page. The vendoring ones matter most: if the manifest is not
rewritten, the site looks self-hosted and still calls out to a CDN.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mkdocs.commands.build import build
from mkdocs.config import load_config
from mkdocs.exceptions import Abort, ConfigurationError, PluginError

import mkdocs_ask.plugin as plugin_module
from mkdocs_ask.vendor import VendoredRuntime

MINIMAL_PAGE = """# Title

A paragraph with enough words in it to survive the minimum chunk length comfortably.

## Section

Another paragraph, also long enough to be indexed as its own chunk in the output.
"""


def minimal_site(tmp_path: Path, *, plugins: list, extra: str = "") -> Path:
    """Write a two-heading site so a build finishes in milliseconds."""
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "index.md").write_text(MINIMAL_PAGE, encoding="utf-8")
    config = tmp_path / "mkdocs.yml"
    config.write_text(f"site_name: Minimal\n{extra}", encoding="utf-8")
    return config


def build_minimal(tmp_path: Path, ask: dict | None, *, with_search: bool = True) -> Path:
    plugins: list = [{"search": {}}] if with_search else []
    plugins.append({"ask": ask} if ask is not None else "ask")
    cfg = load_config(
        config_file=str(minimal_site(tmp_path, plugins=plugins)),
        site_dir=str(tmp_path / "site"),
        plugins=plugins,
    )
    build(cfg)
    return tmp_path / "site" / "assets" / "ask"


def read_manifest(out: Path) -> dict:
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


# -- configuration errors --------------------------------------------------------------------


def test_an_unknown_embedding_model_stops_the_build(tmp_path):
    # Better to fail here than to ship an index the browser cannot query.
    with pytest.raises((PluginError, ConfigurationError, Abort)):
        build_minimal(tmp_path, {"embedding": {"model": "multilingual-e5-smal"}})


def test_a_hub_id_without_dimensions_stops_the_build(tmp_path):
    with pytest.raises((PluginError, ConfigurationError, Abort)):
        build_minimal(tmp_path, {"embedding": {"model": "someone/custom", "backend": "none"}})


def test_building_without_a_search_plugin_warns_that_there_is_nothing_to_attach_to(
    tmp_path, caplog
):
    # The theme's search field is the only entry point, and themes render it only when a
    # search plugin is active, so this is the difference between working and silently inert.
    with caplog.at_level("WARNING"):
        build_minimal(tmp_path, {"embedding": {"backend": "none"}}, with_search=False)
    assert "no search plugin is enabled" in caplog.text


def test_material_search_counts_as_a_search_plugin(tmp_path, caplog):
    # Material registers its own under "material/search"; matching on the bare name would
    # warn on every Material site.
    assert plugin_module.AskPlugin  # imported for clarity
    plugins = [{"material/search": {}}, {"ask": {"embedding": {"backend": "none"}}}]
    cfg = load_config(
        config_file=str(minimal_site(tmp_path, plugins=plugins)),
        site_dir=str(tmp_path / "site"),
        theme={"name": "material"},
        plugins=plugins,
    )
    with caplog.at_level("WARNING"):
        build(cfg)
    assert "no search plugin is enabled" not in caplog.text


# -- cache_dir resolution ---------------------------------------------------------------------


def test_a_relative_cache_dir_resolves_next_to_mkdocs_yml(tmp_path, monkeypatch):
    # Resolving against the process working directory instead would scatter caches wherever
    # the build happened to be started from.
    monkeypatch.setattr(plugin_module, "create_embedder", lambda *a, **k: _FakeEmbedder())
    build_minimal(tmp_path, {"embedding": {"cache_dir": ".cache/here"}})
    assert (tmp_path / ".cache" / "here" / "embeddings").is_dir()


class _FakeEmbedder:
    backend = "fake"
    model_id = "fake:v1"

    def embed(self, texts):
        import numpy as np

        return np.eye(len(texts), 8, dtype=np.float32)


# -- vendoring ---------------------------------------------------------------------------------


def test_vendoring_rewrites_the_manifest_to_local_paths(tmp_path, monkeypatch):
    captured = {}

    def fake_vendor_runtime(**kwargs):
        captured.update(kwargs)
        return VendoredRuntime(
            transformers_url="vendor/transformers/transformers.min.js",
            wasm_paths="vendor/transformers/",
            model_base_url="vendor/models/",
            bytes_written=1234,
        )

    monkeypatch.setattr(plugin_module, "create_embedder", lambda *a, **k: _FakeEmbedder())
    monkeypatch.setattr(plugin_module, "vendor_runtime", fake_vendor_runtime)

    out = build_minimal(tmp_path, {"runtime": {"vendor": True}})
    runtime = read_manifest(out)["runtime"]

    assert runtime["transformers_url"] == "vendor/transformers/transformers.min.js"
    assert runtime["wasm_paths"] == "vendor/transformers/"
    assert runtime["model_base_url"] == "vendor/models/"
    assert captured["out_dir"] == tmp_path / "site" / "assets" / "ask"
    assert captured["browser_dtype"] == "q8"
    assert captured["spec"].onnx_id == "Xenova/multilingual-e5-small"


def test_vendoring_without_a_model_leaves_the_model_host_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(
        plugin_module,
        "vendor_runtime",
        lambda **k: VendoredRuntime(
            transformers_url="vendor/transformers/transformers.min.js",
            wasm_paths="vendor/transformers/",
            model_base_url=None,
            bytes_written=1,
        ),
    )
    out = build_minimal(tmp_path, {"runtime": {"vendor": True}, "embedding": {"backend": "none"}})
    runtime = read_manifest(out)["runtime"]
    assert runtime["wasm_paths"] == "vendor/transformers/"
    assert runtime["model_base_url"] is None


def test_a_failed_vendoring_leaves_the_cdn_urls_in_place(tmp_path, monkeypatch):
    # Falling back loudly beats publishing a site whose model URLs point at nothing.
    monkeypatch.setattr(plugin_module, "vendor_runtime", lambda **k: None)
    out = build_minimal(tmp_path, {"runtime": {"vendor": True}, "embedding": {"backend": "none"}})
    runtime = read_manifest(out)["runtime"]
    assert runtime["transformers_url"].startswith("https://cdn.jsdelivr.net/")
    assert runtime["wasm_paths"] is None


def test_vendoring_warns_that_it_does_not_cover_the_answer_model(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(plugin_module, "vendor_runtime", lambda **k: None)
    with caplog.at_level("WARNING"):
        build_minimal(
            tmp_path,
            {
                "runtime": {"vendor": True},
                "embedding": {"backend": "none"},
                "ai_answer": {"enabled": True},
            },
        )
    assert "does not cover the answer model" in caplog.text


def test_no_vendoring_means_vendor_runtime_is_never_called(tmp_path, monkeypatch):
    def fail(**kwargs):
        raise AssertionError("vendor_runtime must not run when runtime.vendor is false")

    monkeypatch.setattr(plugin_module, "vendor_runtime", fail)
    out = build_minimal(tmp_path, {"embedding": {"backend": "none"}})
    assert read_manifest(out)["runtime"]["vendor"] is False


def test_a_missing_embedding_extra_still_produces_a_keyword_index(tmp_path, monkeypatch, caplog):
    """`backend: auto` with nothing installed must degrade, not fail.

    This is the path a user hits after `pip install mkdocs-ask` without the extra, so it has
    to leave a working site behind rather than a half-written one.
    """
    monkeypatch.setattr(plugin_module, "create_embedder", lambda *a, **k: None)
    with caplog.at_level("INFO"):
        out = build_minimal(tmp_path, {"embedding": {"backend": "auto"}})

    manifest = read_manifest(out)
    assert manifest["model"] is None
    assert manifest["vectors"] is None
    assert not (out / "vectors.bin").exists()
    assert manifest["chunks"]["count"] > 0
    assert "keyword-only (BM25)" in caplog.text


@pytest.mark.parametrize("spelling", ["./assets/ask", "assets/ask/", "assets//ask", " assets/ask "])
def test_output_dir_spellings_normalise_to_one_layout(tmp_path, spelling):
    """The manifest, the script tags and the directory on disk must all agree.

    A leading "./" or a trailing "/" is easy to type and used to leak into the script src as
    "./assets/ask/ask.js", which browsers tolerate but which made the manifest's own
    `output_dir` disagree with where the files were.
    """
    out = build_minimal(tmp_path, {"embedding": {"backend": "none"}, "output_dir": spelling})
    assert out == tmp_path / "site" / "assets" / "ask"
    assert (out / "manifest.json").is_file()
    assert read_manifest(out)["output_dir"] == "assets/ask"
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'src="assets/ask/ask.js"' in html
    assert "./assets" not in html


def test_embedding_progress_is_logged_under_the_plugin_prefix(tmp_path, monkeypatch, caplog):
    # One prefix for every line the plugin emits, so a log is scannable for "ask:".
    monkeypatch.setattr(plugin_module, "create_embedder", lambda *a, **k: _FakeEmbedder())
    with caplog.at_level("INFO"):
        build_minimal(tmp_path, {})
    lines = [r.getMessage() for r in caplog.records if "embedded" in r.getMessage()]
    assert lines, "the embedding summary was not logged"
    assert all(line.startswith("ask: ") for line in lines), lines
    assert not any("mkdocs-ask:" in line for line in lines), "the old ad hoc prefix is gone"
