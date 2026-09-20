"""MkDocs plugin entry point: `plugins: [ask]`."""

from __future__ import annotations

import fnmatch
import os
import posixpath
from datetime import datetime, timezone
from pathlib import Path

from mkdocs.config.config_options import ExtraScriptValue
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.exceptions import PluginError
from mkdocs.plugins import BasePlugin, get_plugin_logger
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page

from . import __version__
from .chunker import Chunk, chunk_page
from .config import AskConfig, to_plain
from .embedder import EmbeddingCache, create_embedder, embed_texts, slugify
from .indexer import write_index
from .models import EmbeddingModelSpec, resolve_model
from .vendor import vendor_runtime

log = get_plugin_logger("ask")

ASSETS_DIR = Path(__file__).parent / "assets"
ASSET_FILES = (
    "ask.js",
    "urls.js",
    "worker.js",
    "search-core.js",
    "generator.js",
    "searchbox.js",
    "ask.css",
)


class AskPlugin(BasePlugin[AskConfig]):
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._pages = 0
        self._spec: EmbeddingModelSpec | None = None

    # -- lifecycle -------------------------------------------------------------------------

    def on_config(self, config: MkDocsConfig) -> MkDocsConfig:
        self._validate()
        emb = self.config.embedding
        try:
            self._spec = resolve_model(
                emb.model,
                dim=emb.dim,
                pooling=emb.pooling,
                query_prefix=emb.query_prefix,
                passage_prefix=emb.passage_prefix,
            )
        except ValueError as exc:
            raise PluginError(f"mkdocs-ask: {exc}") from exc

        # The theme's search field is the only entry point, and themes render it only when a
        # search plugin is active. Material registers its own under "material/search".
        if not any(name.rsplit("/", 1)[-1] == "search" for name in config.plugins):
            log.warning(
                "no search plugin is enabled, so most themes render no search field and "
                "mkdocs-ask will have nothing to attach to. Add `- search` to plugins."
            )

        out = self._output_dir
        script = ExtraScriptValue(f"{out}/ask.js")
        script.type = "module"
        config.extra_javascript.append(script)
        config.extra_css.append(f"{out}/ask.css")
        return config

    def on_pre_build(self, *, config: MkDocsConfig) -> None:
        # `mkdocs serve` rebuilds with the same plugin instance.
        self._chunks = []
        self._pages = 0

    def on_files(self, files: Files, /, *, config: MkDocsConfig) -> Files:
        for name in ASSET_FILES:
            files.append(
                File.generated(
                    config, f"{self._output_dir}/{name}", abs_src_path=str(ASSETS_DIR / name)
                )
            )
        return files

    def on_page_content(
        self, html: str, /, *, page: Page, config: MkDocsConfig, files: Files
    ) -> str:
        if self._is_excluded(page.file.src_uri):
            return html
        title = page.title or page.file.name
        self._chunks.extend(
            chunk_page(html, page_url=page.url, page_title=str(title), cfg=self.config.chunking)
        )
        self._pages += 1
        return html

    def on_post_build(self, *, config: MkDocsConfig) -> None:
        assert self._spec is not None
        emb = self.config.embedding
        chunks = self._chunks
        vectors = None
        model_info = None

        if chunks and emb.backend != "none":
            cache_root = Path(self._resolve_path(config, emb.cache_dir))
            embedder = create_embedder(
                self._spec,
                backend=emb.backend,
                browser_dtype=emb.browser_dtype,
                build_dtype=emb.build_dtype,
                cache_dir=cache_root,
            )
            if embedder is not None:
                cache_file = cache_root / "embeddings" / f"{slugify(embedder.model_id)}.npz"
                cache = EmbeddingCache(cache_file)
                texts = [
                    c.embedding_text(self._spec.passage_prefix, emb.include_breadcrumbs)
                    for c in chunks
                ]
                vectors = embed_texts(texts, embedder, cache, batch_size=emb.batch_size)
                model_info = {
                    **self._spec.to_json(),
                    "dim": int(vectors.shape[1]),
                    "browser_dtype": emb.browser_dtype,
                    "build_backend": embedder.backend,
                }

        runtime = to_plain(self.config.runtime)
        if self.config.runtime.vendor:
            vendored = vendor_runtime(
                out_dir=Path(config.site_dir) / self._output_dir,
                cache_dir=Path(self._resolve_path(config, emb.cache_dir)),
                transformers_url=self.config.runtime.transformers_url,
                spec=self._spec if model_info else None,
                browser_dtype=emb.browser_dtype,
            )
            if vendored is not None:
                runtime["transformers_url"] = vendored.transformers_url
                runtime["wasm_paths"] = vendored.wasm_paths
                if vendored.model_base_url:
                    runtime["model_base_url"] = vendored.model_base_url
            if self.config.ai_answer.enabled:
                log.warning(
                    "runtime.vendor does not cover the answer model: ai_answer still loads "
                    "from a CDN. Set runtime.webllm_url and ai_answer.wasm_model_url to "
                    "self-hosted copies, or leave ai_answer off."
                )

        manifest = {
            "generator": f"mkdocs-ask {__version__}",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "language": self.config.language,
            "output_dir": self._output_dir,
            "pages": self._pages,
            "model": model_info,
            "search": to_plain(self.config.search),
            "ai_answer": to_plain(self.config.ai_answer),
            "ui": to_plain(self.config.ui),
            "search_box": to_plain(self.config.search_box),
            "runtime": runtime,
        }
        out_dir = Path(config.site_dir) / self._output_dir
        write_index(out_dir, chunks, vectors, manifest, dtype=emb.dtype)
        mode = "hybrid (BM25 + vectors)" if vectors is not None else "keyword-only (BM25)"
        log.info(f"indexed {len(chunks)} chunks from {self._pages} pages, {mode} -> {out_dir}")

    # -- helpers ---------------------------------------------------------------------------

    @property
    def _output_dir(self) -> str:
        # Normalised once so "./assets/ask", "assets/ask/" and "assets//ask" all mean the same
        # thing in the manifest, the script tags and the on-disk layout.
        return posixpath.normpath(self.config.output_dir.strip()).strip("/")

    def _is_excluded(self, src_uri: str) -> bool:
        return any(fnmatch.fnmatch(src_uri, pattern) for pattern in self.config.exclude)

    @staticmethod
    def _resolve_path(config: MkDocsConfig, path: str) -> str:
        if os.path.isabs(path):
            return path
        base = os.path.dirname(config.config_file_path) if config.config_file_path else os.getcwd()
        return os.path.join(base, path)

    def _validate(self) -> None:
        s = self.config.search
        ch = self.config.chunking
        emb = self.config.embedding
        ai = self.config.ai_answer
        weights = (s.keyword_weight, s.semantic_weight)
        if min(weights) < 0 or sum(weights) <= 0:
            raise PluginError(
                "mkdocs-ask: search.keyword_weight/semantic_weight must be >= 0 and not both 0"
            )
        if s.rrf_k < 1:
            raise PluginError("mkdocs-ask: search.rrf_k must be >= 1")
        if s.top_k < 1 or s.candidates < s.top_k:
            raise PluginError("mkdocs-ask: search.top_k must be >= 1 and <= search.candidates")
        if s.snippet_chars < 1:
            raise PluginError("mkdocs-ask: search.snippet_chars must be >= 1")
        if ch.max_chars < 50 or ch.overlap < 0 or ch.overlap >= ch.max_chars:
            raise PluginError(
                "mkdocs-ask: chunking.max_chars must be >= 50 and overlap < max_chars"
            )
        if ch.min_chars < 0:
            raise PluginError("mkdocs-ask: chunking.min_chars must be >= 0")
        if emb.batch_size < 1:
            raise PluginError("mkdocs-ask: embedding.batch_size must be >= 1")
        if ai.max_tokens < 1 or ai.context_chunks < 1:
            raise PluginError("mkdocs-ask: ai_answer.max_tokens and context_chunks must be >= 1")
        sb = self.config.search_box
        if sb.min_chars < 1 or sb.max_results < 1 or sb.debounce_ms < 0:
            raise PluginError(
                "mkdocs-ask: search_box.min_chars and max_results must be >= 1, "
                "debounce_ms must be >= 0"
            )
        if not self._output_dir_ok(self.config.output_dir):
            raise PluginError("mkdocs-ask: output_dir must be a relative path inside the site")

    @staticmethod
    def _output_dir_ok(path: str) -> bool:
        p = posixpath.normpath(path.strip()).strip("/")
        return bool(p) and p != "." and not os.path.isabs(path) and ".." not in p.split("/")
