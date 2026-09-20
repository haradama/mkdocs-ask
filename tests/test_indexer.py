from __future__ import annotations

import json

import numpy as np

from mkdocs_ask.chunker import Chunk
from mkdocs_ask.embedder import EmbeddingCache, embed_texts
from mkdocs_ask.indexer import INT8_SCALE, dequantize_int8, quantize_int8, write_index


class FakeEmbedder:
    backend = "fake"
    model_id = "fake:v1"
    calls = 0

    def embed(self, texts):
        self.calls += 1
        rng = np.random.default_rng([abs(hash(t)) % (2**32) for t in texts])
        v = rng.standard_normal((len(texts), 8)).astype(np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def make_chunks(n):
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


def test_int8_roundtrip_keeps_cosine_close():
    rng = np.random.default_rng(0)
    v = rng.standard_normal((50, 384)).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    q = quantize_int8(v)
    assert q.dtype == np.int8
    back = dequantize_int8(q)
    cos = np.sum(v * back, axis=1)
    assert cos.min() > 0.99
    assert INT8_SCALE == 1.0 / 127.0


def test_write_index_files(tmp_path):
    chunks = make_chunks(3)
    vectors = np.eye(3, 4, dtype=np.float32)
    manifest = write_index(tmp_path, chunks, vectors, {"language": "ja"}, dtype="int8")
    assert (tmp_path / "manifest.json").exists()
    assert json.loads((tmp_path / "chunks.json").read_text())[1]["url"] == "p1/#h"
    raw = (tmp_path / "vectors.bin").read_bytes()
    assert len(raw) == 3 * 4  # int8
    assert manifest["vectors"] == {
        "file": "vectors.bin",
        "dtype": "int8",
        "count": 3,
        "dim": 4,
        "scale": INT8_SCALE,
        "byte_order": "little",
    }
    assert manifest["chunks"]["count"] == 3
    assert manifest["version"] == 1


def test_write_index_without_vectors(tmp_path):
    manifest = write_index(tmp_path, make_chunks(2), None, {})
    assert manifest["vectors"] is None
    assert not (tmp_path / "vectors.bin").exists()


def test_embedding_cache_reuses_vectors(tmp_path):
    texts = ["a", "b", "c"]
    embedder = FakeEmbedder()
    cache_path = tmp_path / "cache.npz"

    first = embed_texts(texts, embedder, EmbeddingCache(cache_path), batch_size=2)
    assert first.shape == (3, 8)
    assert embedder.calls == 2  # two batches
    assert cache_path.exists()

    cache = EmbeddingCache(cache_path)
    assert len(cache) == 3
    second = embed_texts(texts + ["d"], embedder, cache, batch_size=10)
    assert embedder.calls == 3  # only "d" computed
    np.testing.assert_allclose(first, second[:3])
