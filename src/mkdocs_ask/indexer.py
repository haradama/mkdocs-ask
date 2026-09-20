"""Serialise chunks and vectors into the static files the browser runtime loads."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .chunker import Chunk

INDEX_VERSION = 1
MANIFEST_FILE = "manifest.json"
CHUNKS_FILE = "chunks.json"
VECTORS_FILE = "vectors.bin"

INT8_SCALE = 1.0 / 127.0


def quantize_int8(vectors: np.ndarray[Any, Any]) -> np.ndarray[Any, np.dtype[np.int8]]:
    """Symmetric int8 quantisation of unit-length vectors (dot = sum(q*x) * INT8_SCALE)."""
    return np.clip(np.rint(vectors * 127.0), -127, 127).astype(np.int8)


def dequantize_int8(q: np.ndarray[Any, Any]) -> np.ndarray[Any, np.dtype[np.float32]]:
    return q.astype(np.float32) * INT8_SCALE


def write_index(
    out_dir: Path,
    chunks: Sequence[Chunk],
    vectors: np.ndarray[Any, Any] | None,
    manifest: dict[str, Any],
    *,
    dtype: str = "int8",
) -> dict[str, Any]:
    """Write manifest.json, chunks.json and (optionally) vectors.bin into `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / CHUNKS_FILE, "w", encoding="utf-8") as f:
        json.dump([c.to_json() for c in chunks], f, ensure_ascii=False, separators=(",", ":"))

    vectors_entry = None
    if vectors is not None and vectors.size:
        if vectors.shape[0] != len(chunks):
            raise ValueError(f"{vectors.shape[0]} vectors for {len(chunks)} chunks")
        if dtype == "int8":
            payload = quantize_int8(vectors)
            scale = INT8_SCALE
        elif dtype == "float32":
            payload = np.ascontiguousarray(vectors, dtype="<f4")
            scale = 1.0
        else:
            raise ValueError(f"unsupported vector dtype {dtype!r}")
        (out_dir / VECTORS_FILE).write_bytes(payload.tobytes(order="C"))
        vectors_entry = {
            "file": VECTORS_FILE,
            "dtype": dtype,
            "count": int(vectors.shape[0]),
            "dim": int(vectors.shape[1]),
            "scale": scale,
            "byte_order": "little",
        }

    manifest = {
        "version": INDEX_VERSION,
        **manifest,
        "chunks": {"file": CHUNKS_FILE, "count": len(chunks)},
        "vectors": vectors_entry,
    }
    with open(out_dir / MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest
