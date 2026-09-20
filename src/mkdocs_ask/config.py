"""Configuration schema for the `ask` plugin (mkdocs.yml -> plugins.ask)."""

from __future__ import annotations

from typing import Any

from mkdocs.config import base
from mkdocs.config import config_options as c

_Number = (int, float)


class EmbeddingConfig(base.Config):
    """Embedding model used at build time (documents) and in the browser (queries).

    Both sides must use the same model so that query and passage vectors live in the
    same space. `model` is a short name from `mkdocs_ask.models.REGISTRY` or a raw
    Hugging Face id (then `dim`/`pooling`/prefixes should be given explicitly).
    """

    model = c.Type(str, default="multilingual-e5-small")
    backend = c.Choice(("auto", "fastembed", "sentence-transformers", "none"), default="auto")
    # Storage dtype of document vectors shipped to the browser.
    dtype = c.Choice(("int8", "float32"), default="int8")
    # Transformers.js dtype for the browser-side query encoder.
    browser_dtype = c.Choice(("q8", "fp16", "fp32", "q4"), default="q8")
    # ONNX file used by the fastembed backend at build time ("auto" = q8 if browser_dtype
    # is q8, else fp32), so build- and browser-side vectors come from the same weights.
    build_dtype = c.Choice(("auto", "fp32", "q8"), default="auto")
    cache_dir = c.Type(str, default=".cache/mkdocs-ask")
    batch_size = c.Type(int, default=32)
    # Prepend "Page > Section > Subsection" to each chunk before embedding.
    include_breadcrumbs = c.Type(bool, default=True)
    # Overrides for models not in the registry.
    dim = c.Optional(c.Type(int))
    pooling = c.Optional(c.Choice(("mean", "cls")))
    query_prefix = c.Optional(c.Type(str))
    passage_prefix = c.Optional(c.Type(str))


class ChunkingConfig(base.Config):
    """Heading-aware chunking of the rendered page HTML."""

    max_chars = c.Type(int, default=800)
    overlap = c.Type(int, default=80)
    min_chars = c.Type(int, default=20)
    include_code = c.Type(bool, default=True)


class SearchConfig(base.Config):
    """Browser-side retrieval settings (shipped to the runtime via manifest.json)."""

    hybrid = c.Type(bool, default=True)
    # "rrf" (reciprocal rank fusion) combines positions and ignores raw score scales, which is
    # what you want when BM25 is unbounded and cosine similarities sit in a narrow band.
    # "weighted" combines min-max normalised scores and reacts to margins, not just order.
    fusion = c.Choice(("rrf", "weighted"), default="rrf")
    rrf_k = c.Type(int, default=60)
    keyword_weight = c.Type(_Number, default=0.4)
    semantic_weight = c.Type(_Number, default=0.6)
    top_k = c.Type(int, default=5)
    # Candidate pool size taken from each retriever before fusion.
    candidates = c.Type(int, default=50)
    snippet_chars = c.Type(int, default=200)


class AiAnswerConfig(base.Config):
    """Phase 2: generate an answer from the retrieved chunks with an on-device SLM."""

    enabled = c.Type(bool, default=False)
    # WebLLM prebuilt model id (WebGPU path).
    model = c.Type(str, default="Qwen2.5-0.5B-Instruct-q4f16_1-MLC")
    backend = c.Choice(("auto", "webgpu", "wasm"), default="auto")
    # GGUF URL for the pure-WASM path (wllama). Required when backend resolves to "wasm".
    wasm_model_url = c.Optional(c.Type(str))
    max_tokens = c.Type(int, default=512)
    temperature = c.Type(_Number, default=0.2)
    context_chunks = c.Type(int, default=5)
    system_prompt = c.Optional(c.Type(str))


class UiConfig(base.Config):
    """The conversation panel, which the theme's search field opens on demand.

    There is no launcher of the plugin's own: the theme's search field is the only entry
    point, so the plugin adds nothing to the page until the reader uses the box they already
    know about.
    """

    title = c.Type(str, default="Ask the docs")
    placeholder = c.Type(str, default="Ask a question about these docs...")
    # When to start downloading the query encoder: when the reader focuses the theme's search
    # field, when the browser is idle after load, or not until the first query.
    preload = c.Choice(("focus", "idle", "query"), default="focus")
    # Overrides for the UI strings, e.g. {found: "...", none: "...", sources: "..."}.
    # The runtime ships English defaults; this is how a site localises the panel.
    strings = c.DictOfItems(c.Type(str), default={})


class SearchBoxConfig(base.Config):
    """How the plugin extends the theme's own search field, which is its only entry point.

    The built-in `search` plugin keeps working untouched: it still owns the input and its own
    result list. This adds ask's semantic hits as a sibling block immediately above that list,
    so a reader who types in the box they already know gets both kinds of answer. Nothing is
    injected inside theme-owned nodes, which is what keeps it from fighting the theme's
    re-renders.
    """

    # "augment" runs the hybrid search as the reader types and lists the top hits.
    # "handoff" adds only a button that opens the ask panel with the typed text, so no model
    # is downloaded until the reader actually asks for it.
    mode = c.Choice(("augment", "handoff"), default="augment")
    # CSS selector for the search input, or "auto" to detect a known theme.
    selector = c.Type(str, default="auto")
    # CSS selector for the theme's result list; the ask block is inserted just before it.
    results_selector = c.Type(str, default="auto")
    min_chars = c.Type(int, default=3)
    debounce_ms = c.Type(int, default=200)
    max_results = c.Type(int, default=3)


class RuntimeConfig(base.Config):
    """Where the browser runtime loads libraries and models from (self-hostable)."""

    transformers_url = c.Type(
        str, default="https://cdn.jsdelivr.net/npm/@huggingface/transformers@3"
    )
    webllm_url = c.Type(str, default="https://esm.run/@mlc-ai/web-llm")
    wllama_url = c.Type(str, default="https://cdn.jsdelivr.net/npm/@wllama/wllama@2/esm/index.js")
    # Base URL to fetch model files from instead of huggingface.co (layout: <base>/<model-id>/...).
    model_base_url = c.Optional(c.Type(str))
    # Directory holding the ONNX Runtime .wasm binaries. Transformers.js points these at
    # jsDelivr on its own, so self-hosting the library alone still leaves this one request
    # going to a CDN. Set it whenever transformers_url is self-hosted.
    wasm_paths = c.Optional(c.Type(str))
    # Copy the library, the ONNX Runtime binaries and the model into the site at build time,
    # so the published site makes no cross-origin request at all. Adds about 160 MB to
    # site/, needs the network on the first build only, and is the only way to stop a reader's
    # IP address and a Referer naming this site from reaching jsDelivr and huggingface.co.
    vendor = c.Type(bool, default=False)
    # Strip the Referer from the runtime's own cross-origin requests. This covers the model
    # and wasm downloads, but not the dynamic import() of the library itself, which no
    # in-page setting can control. Vendoring is what removes that last one.
    no_referrer = c.Type(bool, default=True)
    device = c.Choice(("auto", "webgpu", "wasm"), default="auto")


class AskConfig(base.Config):
    # BCP 47 tag recorded in the manifest. Informational today; reserved for
    # language-specific tokenisation. UI wording is set through `ui.strings`.
    language = c.Type(str, default="en")
    # Directory (relative to site root) receiving runtime assets and index files.
    output_dir = c.Type(str, default="assets/ask")
    # fnmatch patterns matched against the page's src_uri (e.g. "changelog.md", "api/*").
    exclude = c.ListOfItems(c.Type(str), default=[])
    embedding = c.SubConfig(EmbeddingConfig)
    chunking = c.SubConfig(ChunkingConfig)
    search = c.SubConfig(SearchConfig)
    ai_answer = c.SubConfig(AiAnswerConfig)
    ui = c.SubConfig(UiConfig)
    search_box = c.SubConfig(SearchBoxConfig)
    runtime = c.SubConfig(RuntimeConfig)


def to_plain(cfg: base.Config) -> dict[str, Any]:
    """Recursively convert a validated Config into JSON-serialisable dicts."""
    out: dict[str, Any] = {}
    for key, value in cfg.items():
        out[key] = to_plain(value) if isinstance(value, base.Config) else value
    return out
