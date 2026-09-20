"""Tests for the build-time embedding layer.

The backend classes wrap third-party libraries, but the decisions inside them are ours: which
ONNX file to load, whether the model needs registering, and above all which pooling to use. A
wrong pooling produces vectors that are silently, subtly wrong, so those lines are stubbed and
asserted rather than left to a real download.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from mkdocs_ask import embedder
from mkdocs_ask.embedder import (
    EmbeddingCache,
    cache_key,
    create_embedder,
    embed_texts,
    l2_normalize,
    slugify,
)
from mkdocs_ask.models import resolve_model

SPEC = resolve_model("multilingual-e5-small")
CLS_SPEC = resolve_model("bge-small-en-v1.5")


# -- normalisation ---------------------------------------------------------------------------


def test_l2_normalize_produces_unit_vectors():
    out = l2_normalize(np.array([[3.0, 4.0], [1.0, 0.0]]))
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), [1.0, 1.0], atol=1e-6)
    np.testing.assert_allclose(out[0], [0.6, 0.8], atol=1e-6)
    assert out.dtype == np.float32


def test_l2_normalize_leaves_a_zero_vector_alone_instead_of_dividing_by_zero():
    out = l2_normalize(np.zeros((1, 4)))
    assert not np.isnan(out).any()
    np.testing.assert_array_equal(out, np.zeros((1, 4), dtype=np.float32))


# -- backend construction --------------------------------------------------------------------


class _FakeTextEmbedding:
    """Stands in for fastembed.TextEmbedding, recording what the plugin asked it to do."""

    registered: list[dict] = []
    constructed: list[dict] = []
    supported = [{"model": "already/known"}]

    def __init__(self, model_name, cache_dir):
        type(self).constructed.append({"model_name": model_name, "cache_dir": cache_dir})

    @classmethod
    def list_supported_models(cls):
        return cls.supported

    @classmethod
    def add_custom_model(cls, **kwargs):
        cls.registered.append(kwargs)

    def embed(self, documents, batch_size):
        # Unnormalised on purpose: the plugin is responsible for normalising.
        return [np.full(4, float(len(d)) or 1.0) for d in documents]


def install_fake_fastembed(monkeypatch, *, supported=None):
    _FakeTextEmbedding.registered = []
    _FakeTextEmbedding.constructed = []
    _FakeTextEmbedding.supported = supported if supported is not None else [{"model": "other"}]

    pooling = types.SimpleNamespace(CLS="CLS", MEAN="MEAN")
    desc = types.ModuleType("fastembed.common.model_description")
    desc.ModelSource = lambda hf=None: {"hf": hf}
    desc.PoolingType = pooling
    common = types.ModuleType("fastembed.common")
    common.model_description = desc
    root = types.ModuleType("fastembed")
    root.TextEmbedding = _FakeTextEmbedding
    root.common = common

    monkeypatch.setitem(sys.modules, "fastembed", root)
    monkeypatch.setitem(sys.modules, "fastembed.common", common)
    monkeypatch.setitem(sys.modules, "fastembed.common.model_description", desc)
    return _FakeTextEmbedding


def test_fastembed_registers_the_browsers_onnx_repository(tmp_path, monkeypatch):
    fake = install_fake_fastembed(monkeypatch)
    emb = create_embedder(
        SPEC, backend="fastembed", browser_dtype="q8", build_dtype="auto", cache_dir=tmp_path
    )

    assert emb.backend == "fastembed"
    # The identity must name the exact weights, so the cache cannot be shared across dtypes.
    assert emb.model_id == "Xenova/multilingual-e5-small:onnx/model_quantized.onnx"
    assert len(fake.registered) == 1
    registered = fake.registered[0]
    assert registered["model"] == "Xenova/multilingual-e5-small"
    assert registered["model_file"] == "onnx/model_quantized.onnx"
    assert registered["dim"] == 384
    assert registered["normalization"] is True
    assert registered["pooling"] == "MEAN"
    assert fake.constructed[0]["model_name"] == "Xenova/multilingual-e5-small"


def test_fastembed_maps_cls_pooling_for_models_that_need_it(tmp_path, monkeypatch):
    fake = install_fake_fastembed(monkeypatch)
    create_embedder(
        CLS_SPEC, backend="fastembed", browser_dtype="q8", build_dtype="auto", cache_dir=tmp_path
    )
    assert fake.registered[0]["pooling"] == "CLS"


def test_fastembed_does_not_re_register_a_model_the_library_already_knows(tmp_path, monkeypatch):
    fake = install_fake_fastembed(monkeypatch, supported=[{"model": SPEC.onnx_id}])
    create_embedder(
        SPEC, backend="fastembed", browser_dtype="q8", build_dtype="auto", cache_dir=tmp_path
    )
    assert fake.registered == []


@pytest.mark.parametrize(
    ("browser_dtype", "build_dtype", "expected"),
    [
        ("q8", "auto", "onnx/model_quantized.onnx"),
        ("fp16", "auto", "onnx/model.onnx"),  # auto falls back to fp32 weights
        ("q8", "fp32", "onnx/model.onnx"),
        ("fp32", "q8", "onnx/model_quantized.onnx"),
    ],
)
def test_build_dtype_chooses_the_onnx_file(
    tmp_path, monkeypatch, browser_dtype, build_dtype, expected
):
    fake = install_fake_fastembed(monkeypatch)
    create_embedder(
        SPEC,
        backend="fastembed",
        browser_dtype=browser_dtype,
        build_dtype=build_dtype,
        cache_dir=tmp_path,
    )
    assert fake.registered[0]["model_file"] == expected


def test_fastembed_embed_normalises_what_the_library_returns(tmp_path, monkeypatch):
    install_fake_fastembed(monkeypatch)
    emb = create_embedder(
        SPEC, backend="fastembed", browser_dtype="q8", build_dtype="auto", cache_dir=tmp_path
    )
    out = emb.embed(["abc", "de"])
    assert out.shape == (2, 4)
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), [1.0, 1.0], atol=1e-6)


def install_fake_sentence_transformers(monkeypatch):
    calls = []

    class FakeST:
        def __init__(self, model_id, cache_folder):
            calls.append({"model_id": model_id, "cache_folder": cache_folder})

        def encode(self, texts, batch_size, convert_to_numpy, normalize_embeddings):
            calls.append({"encode": len(texts), "normalize": normalize_embeddings})
            return np.tile(np.array([3.0, 4.0], dtype=np.float32), (len(texts), 1))

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return calls


def test_sentence_transformers_uses_the_original_checkpoint(tmp_path, monkeypatch):
    calls = install_fake_sentence_transformers(monkeypatch)
    emb = create_embedder(
        SPEC,
        backend="sentence-transformers",
        browser_dtype="q8",
        build_dtype="auto",
        cache_dir=tmp_path,
    )
    assert emb.backend == "sentence-transformers"
    assert emb.model_id == "st:intfloat/multilingual-e5-small"
    assert calls[0]["model_id"] == "intfloat/multilingual-e5-small"

    out = emb.embed(["a", "b"])
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), [1.0, 1.0], atol=1e-6)


# -- backend selection -----------------------------------------------------------------------


def block_import(monkeypatch, name):
    """Make `import <name>` fail, as it does when the extra is not installed."""
    monkeypatch.setitem(sys.modules, name, None)


def test_backend_none_skips_embedding_entirely(tmp_path):
    assert (
        create_embedder(
            SPEC, backend="none", browser_dtype="q8", build_dtype="auto", cache_dir=tmp_path
        )
        is None
    )


def test_auto_prefers_fastembed(tmp_path, monkeypatch):
    install_fake_fastembed(monkeypatch)
    install_fake_sentence_transformers(monkeypatch)
    emb = create_embedder(
        SPEC, backend="auto", browser_dtype="q8", build_dtype="auto", cache_dir=tmp_path
    )
    assert emb.backend == "fastembed"


def test_auto_falls_back_to_sentence_transformers(tmp_path, monkeypatch):
    block_import(monkeypatch, "fastembed")
    install_fake_sentence_transformers(monkeypatch)
    emb = create_embedder(
        SPEC, backend="auto", browser_dtype="q8", build_dtype="auto", cache_dir=tmp_path
    )
    assert emb.backend == "sentence-transformers"


def test_auto_degrades_to_keyword_only_with_a_warning(tmp_path, monkeypatch, caplog):
    block_import(monkeypatch, "fastembed")
    block_import(monkeypatch, "sentence_transformers")
    with caplog.at_level("WARNING"):
        result = create_embedder(
            SPEC, backend="auto", browser_dtype="q8", build_dtype="auto", cache_dir=tmp_path
        )
    assert result is None, "a missing extra must not fail the build"
    assert "keyword-only" in caplog.text


@pytest.mark.parametrize("backend", ["fastembed", "sentence-transformers"])
def test_an_explicitly_named_missing_backend_raises(tmp_path, monkeypatch, backend):
    # Asking for a backend by name and silently getting another one would be worse than failing.
    block_import(monkeypatch, "fastembed")
    block_import(monkeypatch, "sentence_transformers")
    with pytest.raises(ImportError):
        create_embedder(
            SPEC, backend=backend, browser_dtype="q8", build_dtype="auto", cache_dir=tmp_path
        )


# -- cache -----------------------------------------------------------------------------------


class CountingEmbedder:
    backend = "counting"
    model_id = "counting:v1"

    def __init__(self):
        self.batches = 0

    def embed(self, texts):
        self.batches += 1
        return l2_normalize(np.array([[float(len(t)), 1.0, 0.0, 0.0] for t in texts]))


def test_cache_key_separates_models_and_texts():
    assert cache_key("a", "x") != cache_key("b", "x")
    assert cache_key("a", "x") != cache_key("a", "y")
    assert cache_key("a", "x") == cache_key("a", "x")


def test_slugify_makes_a_model_id_safe_as_a_filename():
    assert slugify("Xenova/multilingual-e5-small:onnx/model_quantized.onnx") == (
        "Xenova_multilingual-e5-small_onnx_model_quantized.onnx"
    )


def test_cache_ignores_a_corrupt_file_and_rebuilds(tmp_path, caplog):
    path = tmp_path / "cache.npz"
    path.write_bytes(b"this is not an npz archive")
    with caplog.at_level("WARNING"):
        cache = EmbeddingCache(path)
    assert len(cache) == 0
    assert "unreadable embedding cache" in caplog.text

    cache.put("k", np.ones(3, dtype=np.float32))
    cache.save()
    assert len(EmbeddingCache(path)) == 1, "a corrupt cache is replaced, not left broken"


def test_cache_put_is_idempotent_and_save_is_a_no_op_when_unchanged(tmp_path):
    path = tmp_path / "cache.npz"
    cache = EmbeddingCache(path)
    cache.put("k", np.ones(3, dtype=np.float32))
    cache.put("k", np.zeros(3, dtype=np.float32))
    assert len(cache) == 1
    cache.save()
    mtime = path.stat().st_mtime_ns
    cache.save()
    assert path.stat().st_mtime_ns == mtime, "nothing dirty, nothing written"


def test_saving_an_empty_cache_writes_no_file(tmp_path):
    path = tmp_path / "cache.npz"
    EmbeddingCache(path).save()
    assert not path.exists()


def test_embed_texts_batches_and_reuses_the_cache(tmp_path):
    cache = EmbeddingCache(tmp_path / "c.npz")
    emb = CountingEmbedder()
    first = embed_texts(["aa", "bbb", "cccc"], emb, cache, batch_size=2)
    assert first.shape == (3, 4)
    assert emb.batches == 2

    second = embed_texts(["aa", "bbb", "cccc"], emb, cache, batch_size=10)
    assert emb.batches == 2, "everything came from the cache"
    np.testing.assert_allclose(first, second)


def test_embed_texts_works_without_a_cache(tmp_path):
    out = embed_texts(["aa"], CountingEmbedder(), None, batch_size=4)
    assert out.shape == (1, 4)


def test_embed_texts_of_nothing_returns_an_empty_array(tmp_path):
    out = embed_texts([], CountingEmbedder(), EmbeddingCache(tmp_path / "c.npz"))
    assert out.shape == (0, 0)


def test_module_exposes_the_protocol_used_by_the_plugin():
    # The plugin depends on this shape; a rename here would break it silently.
    assert hasattr(embedder, "Embedder")


def test_embed_texts_treats_a_non_positive_batch_size_as_one(tmp_path):
    # Config validation rejects this, but the function is public: a zero here used to loop
    # forever on empty slices, so it must clamp rather than trust its caller.
    emb = CountingEmbedder()
    out = embed_texts(["a", "bb", "ccc"], emb, None, batch_size=0)
    assert out.shape == (3, 4)
    assert emb.batches == 3, "one text per batch"
