"""Coverage for the paths that only appear when something is unusual or wrong.

These are the branches that decide what happens on a typo in `mkdocs.yml`, a model outside the
registry, a half-written cache, or a network that drops mid-build. They run rarely, which is
exactly why they are worth pinning down: a mistake here surfaces as a broken site rather than
as a failing build.
"""

from __future__ import annotations

import io

import numpy as np
import pytest

from mkdocs_ask import vendor
from mkdocs_ask.chunker import chunk_page, split_text
from mkdocs_ask.config import ChunkingConfig
from mkdocs_ask.indexer import write_index
from mkdocs_ask.models import REGISTRY, resolve_model

# -- chunker -------------------------------------------------------------------------------


def cfg(**overrides) -> ChunkingConfig:
    c = ChunkingConfig()
    c.load_dict(overrides)
    assert not c.validate()[0]
    return c


def test_markup_nested_inside_a_skipped_element_is_dropped_entirely():
    """Nested tags inside a skipped element must not reopen collection.

    `<script>` is not a useful case here: HTMLParser treats its body as raw text, so nothing
    inside is ever reported as a tag. `<svg>` is parsed normally, so it is what actually
    exercises the depth counter on both the opening and the closing side.
    """
    html = (
        "<h1 id='h'>Title</h1>"
        "<p>This paragraph is long enough to survive the minimum chunk length.</p>"
        "<svg><g><title>hidden label</title></g><desc>hidden description</desc></svg>"
        "<script>alsoHidden()</script>"
        "<p>This second paragraph is likewise long enough to be kept in the index.</p>"
    )
    chunks = chunk_page(html, page_url="p/", page_title="Title", cfg=cfg())
    text = " ".join(c.text for c in chunks)
    assert "long enough to survive" in text
    assert "likewise long enough" in text
    assert "hidden label" not in text
    assert "hidden description" not in text
    assert "alsoHidden()" not in text


def test_blank_paragraphs_do_not_become_chunks():
    sentence = "This paragraph is long enough to matter for the splitter. "
    text = (sentence * 6) + "\n\n\n\n   \n\n" + (sentence * 6)
    pieces = split_text(text, max_chars=200, overlap=0, min_chars=20)
    assert pieces, "the real paragraphs survive"
    assert all(p.strip() for p in pieces), "no chunk is blank or whitespace only"


# -- indexer -------------------------------------------------------------------------------


def make_chunks(n):
    from mkdocs_ask.chunker import Chunk

    return [
        Chunk(
            url=f"p{i}/#h",
            page_url=f"p{i}/",
            page_title=f"P{i}",
            heading="h",
            breadcrumbs=[f"P{i}", "h"],
            text=f"text {i}",
        )
        for i in range(n)
    ]


def test_float32_vectors_are_written_little_endian_at_full_width(tmp_path):
    vectors = np.eye(3, 4, dtype=np.float32)
    manifest = write_index(tmp_path, make_chunks(3), vectors, {}, dtype="float32")

    assert manifest["vectors"]["dtype"] == "float32"
    assert manifest["vectors"]["scale"] == 1.0
    raw = (tmp_path / "vectors.bin").read_bytes()
    assert len(raw) == 3 * 4 * 4
    np.testing.assert_allclose(np.frombuffer(raw, dtype="<f4").reshape(3, 4), vectors)


def test_a_vector_count_that_disagrees_with_the_chunks_is_refused(tmp_path):
    # Shipping this would silently mis-attribute every search result to the wrong section.
    with pytest.raises(ValueError, match="2 vectors for 3 chunks"):
        write_index(tmp_path, make_chunks(3), np.zeros((2, 4), dtype=np.float32), {})


def test_an_unknown_vector_dtype_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unsupported vector dtype"):
        write_index(tmp_path, make_chunks(1), np.zeros((1, 4), dtype=np.float32), {}, dtype="f16")


# -- models --------------------------------------------------------------------------------


def test_a_raw_hugging_face_id_works_when_the_dimensions_are_given():
    spec = resolve_model("someone/custom-model", dim=512, pooling="cls", query_prefix="Q: ")
    assert spec.name == "someone/custom-model"
    assert spec.onnx_id == "someone/custom-model"
    assert spec.hf_id == "someone/custom-model"
    assert (spec.dim, spec.pooling, spec.query_prefix) == (512, "cls", "Q: ")
    assert spec.passage_prefix == "", "an unspecified prefix stays empty, not inherited"


def test_a_raw_hugging_face_id_without_dimensions_is_refused():
    # Guessing would produce an index whose vectors cannot be compared with the query's.
    with pytest.raises(ValueError, match="set embedding.dim"):
        resolve_model("someone/custom-model")


def test_a_name_that_is_neither_registered_nor_a_hub_id_is_refused():
    with pytest.raises(ValueError, match="Unknown embedding.model"):
        resolve_model("multilingual-e5-smal")  # a typo, not a hub id


def test_registry_overrides_apply_without_mutating_the_registry():
    overridden = resolve_model("multilingual-e5-small", query_prefix="search: ")
    assert overridden.query_prefix == "search: "
    assert REGISTRY["multilingual-e5-small"].query_prefix == "query: "


# -- vendor --------------------------------------------------------------------------------


def fake_network(monkeypatch, bundle: str = 'x={version:"3.8.1"}'):
    requested: list[str] = []

    def fake_urlopen(url, *args, **kwargs):
        requested.append(url)
        return io.BytesIO((bundle if "transformers" in url else "binary").encode())

    monkeypatch.setattr(vendor.urllib.request, "urlopen", fake_urlopen)
    return requested


def test_a_self_hosted_library_url_stops_vendoring_rather_than_guessing(
    tmp_path, monkeypatch, caplog
):
    # The bundle parses fine here, so this really exercises the "cannot locate dist/" branch:
    # guessing a wasm location would leave a CDN request alive while looking self-hosted.
    fake_network(monkeypatch, bundle='transformers x={version:"3.8.1"}')
    with caplog.at_level("WARNING"):
        result = vendor.vendor_runtime(
            out_dir=tmp_path / "site",
            cache_dir=tmp_path / "cache",
            transformers_url="https://intranet.example.com/vendor/transformers.min.js",
            spec=None,
            browser_dtype="q8",
        )
    assert result is None
    assert "not a jsDelivr npm URL" in caplog.text
    assert "runtime.wasm_paths" in caplog.text
    assert not (tmp_path / "site").exists(), "nothing half-written is left behind"


def test_a_network_failure_falls_back_to_the_cdn(tmp_path, monkeypatch, caplog):
    def boom(url, *args, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(vendor.urllib.request, "urlopen", boom)
    with caplog.at_level("WARNING"):
        result = vendor.vendor_runtime(
            out_dir=tmp_path / "site",
            cache_dir=tmp_path / "cache",
            transformers_url="https://cdn.jsdelivr.net/npm/@huggingface/transformers@3",
            spec=None,
            browser_dtype="q8",
        )
    assert result is None
    assert "falling back to the CDN" in caplog.text


def test_optional_model_files_are_vendored_when_the_repository_has_them(tmp_path, monkeypatch):
    fake_network(monkeypatch)
    import huggingface_hub

    hub = tmp_path / "hub"

    def fake_hf_hub_download(repo_id, filename, **kwargs):
        path = hub / repo_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        return str(path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_hf_hub_download)
    spec = resolve_model("multilingual-e5-small")
    out = tmp_path / "site"
    vendor.vendor_runtime(
        out_dir=out,
        cache_dir=tmp_path / "cache",
        transformers_url="https://cdn.jsdelivr.net/npm/@huggingface/transformers@3",
        spec=spec,
        browser_dtype="q8",
    )
    base = out / "vendor/models" / spec.onnx_id
    for name in vendor.MODEL_FILES_OPTIONAL:
        assert (base / name).exists(), name
