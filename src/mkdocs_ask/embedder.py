"""Build-time document embedding: pluggable backends plus an on-disk cache."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from mkdocs.plugins import get_plugin_logger

from .models import ONNX_FILES, EmbeddingModelSpec

log = get_plugin_logger("ask")

FloatArray = np.ndarray[Any, np.dtype[np.float32]]


class Embedder(Protocol):
    backend: str
    model_id: str  # unique identity of the weights (part of the cache key)

    def embed(self, texts: Sequence[str]) -> FloatArray:
        """Return an (n, dim) float32 array of L2-normalised vectors."""
        ...


def l2_normalize(vectors: np.ndarray[Any, Any]) -> FloatArray:
    """Scale each row to unit length; all-zero rows are left as they are."""
    out = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return np.asarray(out / norms, dtype=np.float32)


class FastEmbedEmbedder:
    """ONNX Runtime via fastembed, loading the *browser's* ONNX repository (Xenova/...)."""

    backend = "fastembed"

    def __init__(self, spec: EmbeddingModelSpec, *, model_file: str, cache_dir: Path) -> None:
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType

        self.model_id = f"{spec.onnx_id}:{model_file}"
        known = {m["model"] for m in TextEmbedding.list_supported_models()}
        if spec.onnx_id not in known:
            TextEmbedding.add_custom_model(
                model=spec.onnx_id,
                pooling=PoolingType.CLS if spec.pooling == "cls" else PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=spec.onnx_id),
                dim=spec.dim,
                model_file=model_file,
            )
        self._model = TextEmbedding(model_name=spec.onnx_id, cache_dir=str(cache_dir))

    def embed(self, texts: Sequence[str]) -> FloatArray:
        vectors = list(self._model.embed(list(texts), batch_size=max(1, len(texts))))
        return l2_normalize(np.asarray(vectors, dtype=np.float32))


class SentenceTransformersEmbedder:
    """PyTorch backend using the original checkpoint (heavier, but any ST model works)."""

    backend = "sentence-transformers"

    def __init__(self, spec: EmbeddingModelSpec, *, cache_dir: Path) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_id = f"st:{spec.hf_id}"
        self._model = SentenceTransformer(spec.hf_id, cache_folder=str(cache_dir))

    def embed(self, texts: Sequence[str]) -> FloatArray:
        vectors = self._model.encode(
            list(texts),
            batch_size=max(1, len(texts)),
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return l2_normalize(vectors)


def create_embedder(
    spec: EmbeddingModelSpec,
    *,
    backend: str,
    browser_dtype: str,
    build_dtype: str,
    cache_dir: Path,
) -> Embedder | None:
    """Instantiate the configured backend; `None` means keyword-only search."""
    if backend == "none":
        return None
    models_dir = cache_dir / "models"
    if build_dtype == "auto":
        build_dtype = "q8" if browser_dtype == "q8" else "fp32"
    model_file = ONNX_FILES[build_dtype]

    if backend in ("auto", "fastembed"):
        try:
            return FastEmbedEmbedder(spec, model_file=model_file, cache_dir=models_dir)
        except ImportError:
            if backend == "fastembed":
                raise
    # No condition here: "none" returned above and "fastembed" either returned or re-raised,
    # so only "auto" and "sentence-transformers" can reach this point.
    try:
        return SentenceTransformersEmbedder(spec, cache_dir=models_dir)
    except ImportError:
        if backend == "sentence-transformers":
            raise
    log.warning(
        "no embedding backend available; building a keyword-only index. Install one with "
        "`uv add 'mkdocs-ask[fastembed]'`, or set embedding.backend: none to silence this."
    )
    return None


def cache_key(model_id: str, text: str) -> str:
    return hashlib.sha256(f"{model_id}\n{text}".encode()).hexdigest()


def slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


class EmbeddingCache:
    """Append-only cache of (sha256 -> vector) persisted as a single .npz file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._index: dict[str, int] = {}
        self._rows: list[FloatArray] = []
        self._dirty = False
        if path.exists():
            try:
                with np.load(path) as data:
                    hashes = data["hashes"].tolist()
                    vectors = np.asarray(data["vectors"], dtype=np.float32)
                self._rows = [row for row in vectors]
                self._index = {h: i for i, h in enumerate(hashes)}
            except Exception as exc:  # corrupt cache: rebuild silently
                log.warning(f"ignoring unreadable embedding cache {path} ({exc})")
                self._index, self._rows = {}, []

    def __len__(self) -> int:
        return len(self._rows)

    def get(self, key: str) -> FloatArray | None:
        i = self._index.get(key)
        return None if i is None else self._rows[i]

    def put(self, key: str, vector: np.ndarray[Any, Any]) -> None:
        if key in self._index:
            return
        self._index[key] = len(self._rows)
        self._rows.append(np.asarray(vector, dtype=np.float32))
        self._dirty = True

    def save(self) -> None:
        if not self._dirty or not self._rows:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            self.path,
            hashes=np.array(list(self._index), dtype="U64"),
            vectors=np.vstack(self._rows),
        )
        self._dirty = False


def embed_texts(
    texts: Sequence[str],
    embedder: Embedder,
    cache: EmbeddingCache | None,
    batch_size: int = 32,
) -> FloatArray:
    """Embed `texts`, reusing cached vectors; returns an (n, dim) float32 array."""
    rows: list[FloatArray | None] = [None] * len(texts)
    keys = [cache_key(embedder.model_id, t) for t in texts]
    missing: list[int] = []
    for i, key in enumerate(keys):
        cached = cache.get(key) if cache is not None else None
        if cached is None:
            missing.append(i)
        else:
            rows[i] = cached
    step = max(1, batch_size)
    for start in range(0, len(missing), step):
        idx = missing[start : start + step]
        vectors = embedder.embed([texts[i] for i in idx])
        for i, vector in zip(idx, vectors, strict=True):
            rows[i] = vector
            if cache is not None:
                cache.put(keys[i], vector)
    if cache is not None:
        cache.save()
    log.info(
        f"embedded {len(texts)} chunks ({len(missing)} computed, "
        f"{len(texts) - len(missing)} from cache) with {embedder.model_id}"
    )
    if not rows:
        return np.zeros((0, 0), dtype=np.float32)
    return np.vstack([r for r in rows if r is not None]).astype(np.float32)
