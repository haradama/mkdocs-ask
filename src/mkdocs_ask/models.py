"""Registry of embedding models usable on both sides (build time and browser).

The browser runtime uses Transformers.js, which loads ONNX exports from the Hugging Face
Hub (typically the `Xenova/*` mirrors). At build time we load *the same ONNX repository*
through fastembed's custom-model API, so document and query vectors come from identical
weights. The original checkpoint id (`hf_id`) is only used by the optional
sentence-transformers backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Transformers.js dtype -> file inside the ONNX repo.
ONNX_FILES = {
    "fp32": "onnx/model.onnx",
    "fp16": "onnx/model_fp16.onnx",
    "q8": "onnx/model_quantized.onnx",
    "q4": "onnx/model_q4.onnx",
}


@dataclass(frozen=True)
class EmbeddingModelSpec:
    name: str
    hf_id: str
    onnx_id: str
    dim: int
    pooling: str = "mean"  # "mean" | "cls"
    query_prefix: str = ""
    passage_prefix: str = ""
    max_seq_length: int = 512

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "hf_id": self.hf_id,
            "onnx_id": self.onnx_id,
            "dim": self.dim,
            "pooling": self.pooling,
            "query_prefix": self.query_prefix,
            "passage_prefix": self.passage_prefix,
            "max_seq_length": self.max_seq_length,
        }


REGISTRY: dict[str, EmbeddingModelSpec] = {
    # Multilingual (incl. Japanese). q8 ONNX ~118 MB. Recommended default.
    "multilingual-e5-small": EmbeddingModelSpec(
        name="multilingual-e5-small",
        hf_id="intfloat/multilingual-e5-small",
        onnx_id="Xenova/multilingual-e5-small",
        dim=384,
        query_prefix="query: ",
        passage_prefix="passage: ",
    ),
    "multilingual-e5-base": EmbeddingModelSpec(
        name="multilingual-e5-base",
        hf_id="intfloat/multilingual-e5-base",
        onnx_id="Xenova/multilingual-e5-base",
        dim=768,
        query_prefix="query: ",
        passage_prefix="passage: ",
    ),
    # English-only, small and fast.
    "bge-small-en-v1.5": EmbeddingModelSpec(
        name="bge-small-en-v1.5",
        hf_id="BAAI/bge-small-en-v1.5",
        onnx_id="Xenova/bge-small-en-v1.5",
        dim=384,
        pooling="cls",
    ),
    "all-MiniLM-L6-v2": EmbeddingModelSpec(
        name="all-MiniLM-L6-v2",
        hf_id="sentence-transformers/all-MiniLM-L6-v2",
        onnx_id="Xenova/all-MiniLM-L6-v2",
        dim=384,
        max_seq_length=256,
    ),
}


def resolve_model(
    name: str,
    *,
    dim: int | None = None,
    pooling: str | None = None,
    query_prefix: str | None = None,
    passage_prefix: str | None = None,
) -> EmbeddingModelSpec:
    """Look up a registry entry, or build a spec for a raw Hugging Face id."""
    if name in REGISTRY:
        spec = REGISTRY[name]
    elif "/" in name:
        if dim is None:
            raise ValueError(
                f"embedding.model {name!r} is not in the registry; set embedding.dim "
                f"(and pooling/prefixes if needed). Known models: {', '.join(REGISTRY)}"
            )
        spec = EmbeddingModelSpec(name=name, hf_id=name, onnx_id=name, dim=dim)
    else:
        raise ValueError(
            f"Unknown embedding.model {name!r}. Use one of {', '.join(REGISTRY)} "
            "or a Hugging Face id like 'org/model'."
        )
    return EmbeddingModelSpec(
        name=spec.name,
        hf_id=spec.hf_id,
        onnx_id=spec.onnx_id,
        dim=spec.dim if dim is None else dim,
        pooling=spec.pooling if pooling is None else pooling,
        query_prefix=spec.query_prefix if query_prefix is None else query_prefix,
        passage_prefix=spec.passage_prefix if passage_prefix is None else passage_prefix,
        max_seq_length=spec.max_seq_length,
    )
