# mkdocs-ask design

A MkDocs plugin that adds an "ask the docs" experience to a **serverless** static site.
Chunking and embedding happen at build time; retrieval (and optionally answer generation)
happens entirely in the visitor's browser. In short: a **fully client-side RAG** for MkDocs.

- Python package `mkdocs-ask`, MkDocs plugin name `ask`, import name `mkdocs_ask`
- Package management: [uv](https://docs.astral.sh/uv/), build backend `uv_build`

---

## 1. Goals and scope

### Goals

1. **No server.** Works on GitHub Pages, Cloudflare Pages, S3 and friends. No API server,
   no vector database, no inference endpoint.
2. **Conversational search.** A chat panel answers with the matching sections and deep links.
3. **Retrieval and generation are separate.** Phase 1 is retrieval only: small, fast, and it
   cannot hallucinate. Phase 2 adds an on-device small language model, lazily loaded.
4. **No second search box.** The plugin extends the field the theme already renders. It adds
   no launcher and no widget of its own.
5. **Language-neutral.** The default embedding model is multilingual and the tokenizer treats
   CJK as a first-class case; all shipped UI text is English and fully overridable.
6. **Good at technical prose.** Hybrid retrieval so that `getUserById`,
   `ERR_CONNECTION_RESET` and `--experimental-xxx` still match exactly.

### Non-goals for v0.1

- Approximate nearest neighbour indexes (brute force is fine up to tens of thousands of chunks)
- Multi-turn query rewriting
- Build-time LLM use (summaries, synthetic questions)
- Replacing the built-in `search` plugin. It keeps its input, its index and its results, and
  ask extends that field rather than adding a second one (section 5.9)
- Any UI of the plugin's own on the page: no launcher button, no corner widget, no hotkey

---

## 2. Architecture

```text
                       mkdocs build
                            |
        +-------------------+--------------------+
        |                                        |
  Markdown docs                          mkdocs-ask (Python)
        |                                        |
        |  on_page_content (rendered HTML)       |
        | -------------------------------------> |  heading-aware chunking   (chunker.py)
        |                                        |  embeddings + cache       (embedder.py)
        |                                        |  same ONNX weights as the browser uses
        |                                        v
        |                              site/assets/ask/
        |                                |- manifest.json    config + model metadata
        |                                |- chunks.json      chunk text + citation URLs
        |                                |- vectors.bin      int8 matrix (count x dim)
        |                                |- ask.js, ask.css  UI
        |                                |- worker.js        retrieval engine (Web Worker)
        |                                |- search-core.js   tokenizer / BM25 / fusion
        |                                +- generator.js     Phase 2 (SLM)
        +---------------- site/ -----------------+
                            |
                GitHub Pages / Cloudflare Pages / S3
                            |
                         Browser
                            |
             +--------------+---------------+
             | ask.js (main thread)         |
             |   theme search field hook    |
             |   + <dialog> panel it opens  |
             |        | postMessage         |
             | worker.js (module worker)    |
             |   BM25 ------+               |
             |              +- fuse - top-k |--> results with citations
             |   query embedding -+         |
             |   (Transformers.js:          |
             |    WebGPU, else WASM)        |
             |        | (Phase 2, optional) |
             |   generator.js               |
             |   WebLLM (WebGPU) / wllama   |
             +------------------------------+
```

The core technique is **build-time indexing plus browser-side retrieval**. The language model
is the last thing added, and search quality, speed and payload size do not depend on it.

---

## 3. Build-time pipeline (Python)

### 3.1 MkDocs events

| Event | What happens | Module |
| --- | --- | --- |
| `on_config` | Validate config, resolve the model, append the plugin's JS (`type="module"`) and CSS | `plugin.py` |
| `on_pre_build` | Reset accumulated chunks, because `mkdocs serve` reuses the plugin instance | |
| `on_files` | Register the runtime assets as generated files so they land in `site/` | |
| `on_page_content` | Chunk the rendered HTML of each page by heading | `chunker.py` |
| `on_post_build` | Embed (with cache), then write `manifest.json`, `chunks.json`, `vectors.bin` | `embedder.py`, `indexer.py` |

The obvious choice would be `on_page_markdown`, but this plugin uses **`on_page_content`**, i.e.
the rendered HTML. The reason is heading anchors: the `toc` extension has already assigned every
heading an `id`, so each chunk can carry a deep link such as `guide/auth/#api-keys`. Without it,
citations could only point at whole pages. The built-in `search` plugin works the same way.

### 3.2 Chunking (`chunker.py`)

1. Walk the HTML with `html.parser.HTMLParser`, starting a new section at every `h1`-`h6`.
2. Skip `<script>`, `<style>`, `<svg>`, `<noscript>`, `<template>`. Drop `a.headerlink`
   (the permalink glyph) from heading titles.
3. Keep `<pre>` content verbatim, including newlines and indentation. Block-level tags become
   newlines and table cells become spaces. `include_code: false` drops `<pre>` entirely.
4. Derive **breadcrumbs** from the heading stack, for example
   `["Authentication", "API keys", "Rotating a key"]`. An `h1` equal to the page title is not
   repeated.
5. If a section is longer than `max_chars`, split it by paragraph, then by sentence, then by
   raw character count, carrying `overlap` characters into the next chunk. Sentence boundaries
   cover ASCII `.`/`!`/`?` and the CJK terminators U+3002, U+FF0E, U+FF01, U+FF1F.
6. Discard fragments shorter than `min_chars`.

A chunk looks like this:

```json
{
  "url": "guide/auth/#rotating-a-key",
  "page_url": "guide/auth/",
  "page_title": "Authentication",
  "heading": "Rotating a key",
  "breadcrumbs": ["Authentication", "API keys", "Rotating a key"],
  "text": "Issue the new key first, then revoke the old one..."
}
```

What actually gets embedded is `passage: Authentication > API keys > Rotating a key\n<text>`,
so even a short chunk carries the context of where it sits. Set
`embedding.include_breadcrumbs: false` to embed the bare text instead.

### 3.3 Embeddings (`embedder.py`)

**Key design decision: both sides use the same weights.**

In the browser, Transformers.js loads an ONNX export from the Hugging Face Hub
(`Xenova/multilingual-e5-small` by default). At build time the plugin loads **the same
repository and the same ONNX file** (`onnx/model_quantized.onnx`, i.e. q8, by default) through
fastembed's custom-model API. fastembed is ONNX Runtime only, with no PyTorch dependency, and it
does not ship `intfloat/multilingual-e5-small` as a built-in model, so registering the ONNX
mirror is both the lightest option and the one that guarantees query and document vectors are
comparable.

| backend | Purpose | Extra |
| --- | --- | --- |
| `fastembed` (recommended, first choice for `auto`) | Same ONNX weights as the browser | `mkdocs-ask[fastembed]` |
| `sentence-transformers` | Original checkpoint via PyTorch; use it for arbitrary ST models | `mkdocs-ask[sentence-transformers]` |
| `none` | No embeddings; emit a BM25 keyword-only index | none |

`auto` tries them in that order and, if neither is installed, logs a warning and continues with
a keyword-only index rather than failing the build.

**Cache.** Vectors are keyed by `sha256(model_id + "\n" + text)` and appended to
`.cache/mkdocs-ask/embeddings/<model>.npz`. Unchanged chunks are never recomputed, so repeat
builds are fast. Model files live in `.cache/mkdocs-ask/models/`.

`models.py` holds the registry that maps a short name to `hf_id`, `onnx_id`, dimensions,
pooling and prefixes.

| name | onnx_id | dim | Notes |
| --- | --- | --- | --- |
| `multilingual-e5-small` (default) | `Xenova/multilingual-e5-small` | 384 | Multilingual, about 118 MB at q8 |
| `multilingual-e5-base` | `Xenova/multilingual-e5-base` | 768 | Higher quality, larger download |
| `bge-small-en-v1.5` | `Xenova/bge-small-en-v1.5` | 384 | English only, CLS pooling |
| `all-MiniLM-L6-v2` | `Xenova/all-MiniLM-L6-v2` | 384 | English, smallest |

Anything else is given as `org/model` together with `embedding.dim` (and `pooling` or the
prefixes when they differ from the defaults).

### 3.4 Index output (`indexer.py`)

- Vectors are quantised to **symmetric int8** by default: `q = round(v * 127)` with
  `scale = 1/127`. Both sides are unit vectors, so `dot(q, query) * scale` is cosine similarity.
  A round trip keeps cosine above 0.99, which the test suite asserts.
- `float32` is available but four times larger.

---

## 4. Output format (`site/assets/ask/`)

### `manifest.json`

The single file the runtime reads first. It embeds the `search`, `ai_answer`, `ui` and `runtime`
sections of `mkdocs.yml` verbatim, plus model metadata and the file list.

```json
{
  "version": 1,
  "generator": "mkdocs-ask 0.1.0",
  "generated_at": "2026-09-20T05:15:00+00:00",
  "language": "en",
  "output_dir": "assets/ask",
  "pages": 22,
  "model": {
    "name": "multilingual-e5-small",
    "onnx_id": "Xenova/multilingual-e5-small",
    "dim": 384, "pooling": "mean",
    "query_prefix": "query: ", "passage_prefix": "passage: ",
    "browser_dtype": "q8", "build_backend": "fastembed"
  },
  "search":    { "hybrid": true, "keyword_weight": 0.4, "semantic_weight": 0.6, "top_k": 5, "candidates": 50, "snippet_chars": 200 },
  "ai_answer": { "enabled": false, "model": "Qwen2.5-0.5B-Instruct-q4f16_1-MLC", "backend": "auto" },
  "ui":        { "title": "Ask the docs", "preload": "focus", "strings": {} },
  "search_box":{ "mode": "augment", "selector": "auto", "min_chars": 3, "max_results": 3 },
  "runtime":   { "transformers_url": "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3", "device": "auto" },
  "chunks":    { "file": "chunks.json", "count": 412 },
  "vectors":   { "file": "vectors.bin", "dtype": "int8", "count": 412, "dim": 384, "scale": 0.007874, "byte_order": "little" }
}
```

When `model` and `vectors` are `null`, the runtime performs keyword-only search.

### `chunks.json`

The array from 3.2. The array index is the chunk id and matches the row number in `vectors.bin`.

### `vectors.bin`

A headerless row-major `count x dim` matrix, either `int8` (one byte per element) or
little-endian `float32`.

---

## 5. Browser runtime (JavaScript)

Plain ES modules with no build step. External libraries are loaded lazily from a CDN with
dynamic `import()`.

### 5.1 Modules

| File | Thread | Responsibility |
| --- | --- | --- |
| `ask.js` | main | Fetch the manifest, attach to the theme's search field, own the `<dialog>` chat panel, talk to the worker |
| `worker.js` | module worker | Load the index, build BM25, embed queries with Transformers.js, fuse, build snippets, bridge to Phase 2 |
| `search-core.js` | shared | Pure functions: `tokenize`, `BM25`, `topKDot`, `fuse`, `makeSnippet`. Unit-testable under Node |
| `generator.js` | worker | Phase 2: WebLLM and wllama adapters plus prompt construction |
| `searchbox.js` | main | Extend the theme's own search field, the only entry point (section 5.9) |
| `urls.js` | shared | Resolve vendored dependency paths and strip the Referer from cross-origin fetches |

`ask.js` derives its own location from `import.meta.url`, so the site can be served from a
sub-path and `output_dir` can be changed freely. Chunk URLs are site-root relative and are
resolved against a root computed from the depth of `output_dir`.

### 5.2 Message protocol (main thread to worker)

```text
-> { type: "init",   baseUrl }                 fetch the index, build BM25
-> { type: "warmup" }                          preload the query encoder
-> { type: "search", id, query }
-> { type: "answer", id, query, ids }          Phase 2: generate grounded in these chunk ids
<- { type: "ready",   count, semantic, manifest }
<- { type: "status",  stage, text, progress? } model download progress and similar
<- { type: "results", id, mode, results[] }    mode: "hybrid" | "semantic" | "keyword"
<- { type: "token",   id, text } ... { type: "answer_done", id }
<- { type: "error",   id?, message }
```

### 5.3 Tokenizer spec v1 (`search-core.js`)

BM25 needs the same tokenizer on both the document and the query side, so there is exactly one
implementation, in JavaScript. Documents are not tokenized at build time; the worker tokenizes
`chunks.json` when it starts, which costs a few hundred milliseconds for ten thousand chunks.

1. NFKC-normalise and lower-case, which folds fullwidth forms to ASCII.
2. Take maximal runs of `[letters, digits, _, ., -]`.
3. Split each run into CJK and non-CJK segments.
4. Non-CJK: strip leading and trailing `._-`, emit the whole token, then also emit every
   sub-token of length two or more obtained by splitting on `._-`. So `foo.bar.baz` yields
   `foo.bar.baz`, `foo`, `bar`, `baz`, and `--experimental-xxx` yields `experimental-xxx`,
   `experimental`, `xxx`.
5. CJK (Han, Hiragana, Katakana, Hangul, plus U+30FC and U+3005): overlapping character
   bigrams; a single character emits itself.

This is what makes CJK usable without shipping a morphological analyser, while keeping
identifiers and flags intact for exact matching.

### 5.4 Keyword retrieval: Okapi BM25

`k1 = 1.2`, `b = 0.75`. The inverted index is a `Map<term, {ids[], tfs[]}>` and each document is
`heading + " " + text`.

### 5.5 Vector retrieval

- The query is embedded as `query_prefix + question` through the Transformers.js
  `feature-extraction` pipeline, with the manifest's `pooling` and `normalize: true`.
- Scoring is a brute-force dot product (`topKDot`), multiplied by `scale` for int8. Ten thousand
  chunks at 384 dimensions is under four million multiply-adds, a few milliseconds to low tens
  of milliseconds, and it runs in the worker so the page never janks.

### 5.6 Hybrid fusion (`fuse`)

Take the top `candidates` from each retriever and combine them. Two methods are available.

**`rrf`, the default.** Reciprocal rank fusion scores by position only:

```text
score(d) = keyword_weight / (k + rank_kw(d)) + semantic_weight / (k + rank_sem(d))
```

with `k = search.rrf_k`, 60 by default. A document missing from one list simply contributes
nothing from it.

**`weighted`.** Min-max normalise each score list onto 0..1, then take the weighted sum:

```text
score = keyword_weight * kw_norm + semantic_weight * sem_norm     (defaults 0.4 / 0.6)
```

The reason `rrf` is the default is a measured one. The first implementation used a weighted sum
of scores divided by each list's maximum. It scored *below* semantic search alone on the example
site's golden query set. Cosine similarities from an e5-style model occupy a narrow band, around
0.80 to 0.92, so dividing by the maximum left every candidate near 1.0 and erased the ranking
the vector retriever had just produced, while unbounded BM25 scores kept their spread and
decided the order. Min-max normalisation fixes that specific bug, and `weighted` now matches
`rrf` on hit rate with a better mean reciprocal rank on that corpus. `rrf` remains the default
because it is insensitive to score scale, so swapping the embedding model does not silently
require retuning the weights.

`example/scripts/eval.py` reports both on every run, which is how a change to weights, chunking
or model is meant to be judged.

### 5.7 Loading strategy and fallbacks

```text
page load     nothing heavy: ask.js is a few KB and only manifest.json is fetched
panel opens   start the worker, fetch chunks.json and vectors.bin, build BM25
              with ui.preload: "open" (default) start downloading the query encoder,
              about 118 MB at q8, kept in the browser Cache API afterwards
first query   waits for the encoder, with download progress shown in the status line;
              if it fails, semantic search is disabled and keyword search continues
AI answer     only on click: generator.js plus a few hundred MB of model weights
```

Device selection: `runtime.device: auto` picks `webgpu` when `navigator.gpu` exists, otherwise
`wasm`. On an isolated network, point `runtime.transformers_url` and `runtime.model_base_url` at
self-hosted copies; models are looked up as `model_base_url/<onnx_id>/...`.

### 5.8 UI

**The plugin adds no entry point of its own.** No floating launcher, no corner button, no
hotkey of ours. A documentation site already has one place people go to look things up, and
adding a second one asks readers to learn a new habit and pays for it with a permanent widget
on every page. So the theme's search field is the only way in, and the plugin's visible surface
on a page that nobody searches is nothing at all.

What it owns is the conversation panel that the search field opens: a `<dialog>` shown with
`showModal`, closed with Escape or a backdrop click. Inside are the user's question, the
assistant's reply with the full result list, and, when `ai_answer` is on, a button that
generates a grounded answer.

- Results show breadcrumbs, heading, snippet, a deep link, and which retrieval mode produced
  them.
- It follows both `prefers-color-scheme` and the Material for MkDocs `slate` scheme. Every
  class is prefixed `mkask-` so it cannot collide with a theme.
- All wording comes from `ui.title`, `ui.placeholder` and the `ui.strings` map, so a site in
  any language relabels it without patching the plugin.

### 5.9 Extending the theme's search field

This is how the plugin is reached at all. There is no launcher to click, so if the theme
renders no search field the plugin stays silent, and `on_config` warns at build time when no
search plugin is enabled, because most themes render the field only when one is.

The built-in `search` plugin is not replaced or wrapped. It keeps its input, its index and its
own result list, and continues to answer exactly as before. The plugin inserts **one block as a
sibling immediately before the theme's result list**, never inside any theme-owned node:

```text
 [data-md-component="search-result"]        (Material)
   |- .md-search-result__meta               theme's "N matching documents"
   |- section.mkask-sb                      <-- inserted here
   |    |- "Semantic matches" + "Ask the docs: <query>" button
   |    +- top N ask hits, deep-linked
   +- .md-search-result__list               theme's own results, untouched
```

Staying outside the theme's nodes is what makes this safe. A theme that rewrites its result
list on every keystroke cannot wipe the block, and the theme's own keyboard navigation over its
list is unaffected because our items are not in it.

**Theme detection.** `selector: auto` tries a table of adapters in order and takes the first
whose input exists. The selectors were read off the markup these themes actually render:

| Theme | Input | Result list | Notes |
| --- | --- | --- | --- |
| Material | `[data-md-component="search-query"]` | `.md-search-result__list` | Overlay closed through the `[data-md-toggle="search"]` checkbox |
| mkdocs | `#mkdocs-search-query` | `#mkdocs-search-results` | |
| readthedocs | `input[name="q"]` | none | Ordinary pages have only a sidebar field; its search page matches the mkdocs row above |
| any other | `input[type="search"]` | none | Handoff button next to the field |

`selector` and `results_selector` accept explicit CSS for a theme that is not in the table.
With no result list, the block is appended next to the input and the reader still gets the
handoff button.

**Two modes.**

- `augment` runs the hybrid search as the reader types, debounced by `debounce_ms` and ignored
  below `min_chars`, and lists the top `max_results` hits. Responses carry a token so a slow
  reply to an earlier keystroke cannot overwrite a newer one.
- `handoff` runs no search at all. It shows only a button that opens the panel with the typed
  text, so nothing is downloaded until the reader asks for it. This is the option for a site
  that does not want typing in the search box to pull a 118 MB model.

The block reuses the panel's worker, so there is one index fetch and one query encoder per
page however the reader arrives.

`ui.preload` decides when the query encoder starts downloading. `focus`, the default, starts
when the reader focuses the search field, which is the earliest honest signal that a search is
coming. `idle` starts after page load when the browser is idle. `query` waits for the first
real question. Handoff mode ignores all of this and downloads nothing until the panel opens,
which is the whole point of it.

Because this code lives inside someone else's DOM, it is covered by DOM tests in
`tests/js/searchbox-dom.test.mjs` that run against markup copied from Material's real output,
including the case where the theme replaces its result list mid-session.

---

## 6. Phase 2: AI answers (`generator.js`)

With `ai_answer.enabled: true`, an "Answer with AI" button appears under the results. Only when
it is clicked does anything load:

```text
question + top-k chunks
   |
system: answer only from the supplied documents, say so when they do not cover it,
        cite sources as [n], reply in the language of the question
user:   "Documents:\n[1] Authentication > API keys\n...\n\nQuestion: ..."
   |
WebGPU available -> WebLLM (@mlc-ai/web-llm) with ai_answer.model, an MLC prebuilt id
otherwise        -> wllama (@wllama/wllama, llama.cpp on WASM) with ai_answer.wasm_model_url, a GGUF
   | streaming
answer, with [n] rewritten as links to the cited sections, plus a source list
```

- Generation is strictly separate from retrieval. With `enabled: false`, `generator.js` is never
  even fetched.
- The default model is `Qwen2.5-0.5B-Instruct-q4f16_1-MLC`.
- This path is **experimental**: there is no automated browser test for it yet.

---

## 7. Configuration reference

```yaml
plugins:
  - search                       # coexists with ask; ask does not replace it
  - ask:
      language: en               # recorded in the manifest; reserved for future tokenisation
      output_dir: assets/ask     # relative to the site root
      exclude:                   # fnmatch patterns against the page's src_uri
        - changelog.md
        - "api/generated/*"

      embedding:
        model: multilingual-e5-small   # registry name or "org/model"
        backend: auto                  # auto | fastembed | sentence-transformers | none
        dtype: int8                    # shipped vectors: int8 | float32
        browser_dtype: q8              # Transformers.js: q8 | fp16 | fp32 | q4
        build_dtype: auto              # ONNX file used at build time: auto | fp32 | q8
        cache_dir: .cache/mkdocs-ask   # relative to mkdocs.yml
        batch_size: 32
        include_breadcrumbs: true
        # For models outside the registry: dim, pooling (mean|cls), query_prefix, passage_prefix

      chunking:
        max_chars: 800
        overlap: 80
        min_chars: 20
        include_code: true

      search:
        hybrid: true
        fusion: rrf                    # rrf | weighted
        rrf_k: 60                      # reciprocal rank fusion constant
        keyword_weight: 0.4
        semantic_weight: 0.6
        top_k: 5
        candidates: 50
        snippet_chars: 200

      ai_answer:
        enabled: false
        model: Qwen2.5-0.5B-Instruct-q4f16_1-MLC   # WebLLM prebuilt id
        backend: auto                  # auto | webgpu | wasm
        wasm_model_url: null           # GGUF URL, required on the wasm path
        max_tokens: 512
        temperature: 0.2
        context_chunks: 5
        system_prompt: null

      ui:                              # the panel the search field opens; no launcher exists
        title: Ask the docs
        placeholder: Ask a question about these docs...
        preload: focus                 # focus | idle | query
        strings: {}                    # override any UI string, e.g. to localise

      search_box:                      # the only entry point (section 5.9)
        mode: augment                  # augment | handoff
        selector: auto                 # auto-detect, or a CSS selector
        results_selector: auto         # the block is inserted just before this element
        min_chars: 3
        debounce_ms: 200
        max_results: 3

      runtime:
        transformers_url: https://cdn.jsdelivr.net/npm/@huggingface/transformers@3
        webllm_url: https://esm.run/@mlc-ai/web-llm
        wllama_url: https://cdn.jsdelivr.net/npm/@wllama/wllama@2/esm/index.js
        model_base_url: null           # self-hosted models: <base>/<onnx_id>/...
        wasm_paths: null               # ONNX Runtime binaries; required if self-hosting by hand
        vendor: false                  # copy every dependency into site/ (section 10)
        no_referrer: true              # strip Referer from the runtime's cross-origin fetches
        device: auto                   # auto | webgpu | wasm
```

`on_config` rejects, with a `PluginError` that stops the build: both weights zero,
`top_k > candidates`, `overlap >= max_chars`, `rrf_k < 1`, a `search_box.min_chars` or
`max_results` below 1, a negative `debounce_ms`, and an `output_dir` that escapes the site.
It warns, without failing, when no search plugin is enabled, since the field the plugin
attaches to would then not exist.

---

## 8. Layout and development workflow (uv)

```text
mkdocs-ask/
|- pyproject.toml            uv_build; entry point mkdocs.plugins -> ask
|- package.json              Node tooling: the JS test harness and markdownlint
|- .markdownlint-cli2.jsonc  Markdown rules, including the Material syntax exceptions
|- .github/workflows/ci.yml  CI; `.actrc` lets `act push` run it locally
|- .github/workflows/release.yml  tag push -> build, verify, publish to PyPI
|- CHANGELOG.md              Keep a Changelog; the release workflow requires an entry
|- DESIGN.md, README.md
|- src/mkdocs_ask/
|  |- plugin.py              AskPlugin, the event handlers
|  |- config.py              configuration schema (mkdocs.config.base.Config)
|  |- models.py              embedding model registry
|  |- chunker.py             HTML -> Chunk
|  |- embedder.py            backends, EmbeddingCache, embed_texts
|  |- indexer.py             manifest/chunks/vectors output, int8 quantisation
|  |- vendor.py              copy the library, wasm and model into the site (section 10)
|  +- assets/                browser runtime, bundled into the wheel
|     |- ask.js  |- worker.js  |- search-core.js  |- urls.js
|     |- generator.js  |- searchbox.js  +- ask.css
|- tests/                    pytest: chunker, indexer, a real build
|  +- js/                    node --test: tokenizer, BM25, fusion, search-box DOM
+- example/                  demo site (23 pages) plus a retrieval evaluation harness
```

```bash
uv sync --all-extras            # dev environment, including fastembed and mkdocs-material
uv run pytest                   # Python tests
uv run pytest --cov             # ... with the 100% coverage gate
npm test                        # JS tests (node --test)
npm run test:coverage           # ... with the 100% coverage gate
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src
npm run lint:md                 # Markdown (markdownlint-cli2); --fix variant: lint:md:fix
act push                        # the whole CI workflow locally, in Docker
uv build                        # dist/*.whl, with assets and the entry point
cd example && uv run mkdocs serve
```

Downstream, installation is one line: `uv add "mkdocs-ask[fastembed]"`.

The repository is ASCII-only. Where CJK characters are functionally required, in the tokenizer's
script test, the sentence-splitting regex and the tests that cover them, they are written as
`\uXXXX` escapes with a comment.

### Continuous integration

`.github/workflows/ci.yml` has five jobs, and every one of them is a command a developer can
run locally.

| Job | What it does |
| --- | --- |
| `lint` | ruff, ruff format, mypy, markdownlint |
| `test-python` | pytest with the 100% gate, on 3.10 through 3.13 |
| `test-js` | node --test with the 100% gate, on Node 22 and 24 |
| `build` | `uv build`, then assert the wheel carries all seven runtime assets and the entry point |
| `example` | a real build of the example site with fastembed, then the retrieval evaluation |

`test-python` deliberately installs **no** embedding extra. The suite stubs both backends, so
running it bare also proves the plugin imports and degrades correctly after a plain
`pip install mkdocs-ask`, which is the most common installation and was previously untested.

`example` is the integration test: it downloads the real model, embeds the real corpus and
scores the golden query set, so a regression in chunking, quantisation or fusion fails the
build. The model is cached by name, so only the first run pays for the download.

The whole workflow runs locally under [act](https://github.com/nektos/act) with `act push`.
`.actrc` supplies the runner image so the invocation needs no flags.

### Releasing

A release is a tag. Pushing `vX.Y.Z` runs `release.yml`, which refuses to continue unless the
tag matches `version` in `pyproject.toml` and `CHANGELOG.md` has a `## [X.Y.Z]` section, then
builds, verifies that the wheel and sdist carry the runtime assets, the entry point and the
license, and publishes through PyPI trusted publishing. No API token exists anywhere; PyPI is
told once which repository, workflow and environment may publish, and GitHub proves it per run
with an OIDC token. The one-time setup is described at the top of the workflow file.

```bash
# bump version in pyproject.toml, move Unreleased notes under the new heading in CHANGELOG.md
git commit -am "Release 0.2.0"
git tag v0.2.0 && git push origin main v0.2.0
```

The build half of that workflow can be rehearsed locally before tagging. `.actrc` scopes a
plain `act push` to CI, so the release workflow is named explicitly and given a tag event:

```bash
echo '{"ref":"refs/tags/v0.2.0"}' > /tmp/tag.json
act push -W .github/workflows/release.yml -j build --eventpath /tmp/tag.json \
    --artifact-server-path /tmp/act-artifacts
```

The publish job needs PyPI's OIDC exchange and cannot run under act, which is the point: a
local run can never publish by accident.

### Coverage

Two gates, both at 100%, over the code that can be exercised without a browser:

| What | Measured | Command |
| --- | --- | --- |
| `src/mkdocs_ask/*.py` | statements and branches | `uv run pytest --cov` |
| `search-core.js`, `searchbox.js`, `urls.js` | lines, branches, functions | `npm run test:coverage` |

Everything in that set runs at build time or is pure, so an uncovered path there is one that
only ever executes on someone else's machine. Turning on branch coverage was worth it on its
own: it found that the `backend: auto` path with no embedding extra installed, which is what a
plain `pip install mkdocs-ask` produces, had never been exercised.

Three modules are deliberately outside the gate, because covering them would mean asserting
against mocks rather than against behaviour:

- `ask.js` and `worker.js` need a real DOM, a Web Worker and Transformers.js. The logic worth
  testing was moved out of them into `search-core.js` and `urls.js`, which are gated; what is
  left is glue.
- `generator.js` needs WebGPU or a GGUF file. `buildMessages`, the part that decides whether
  an answer is grounded and citable, is tested. The two backend adapters are not.

They are checked another way: `node --check` on every module, a real `mkdocs build` of the
example site, and serving that site to confirm every URL the runtime will request returns 200.

One coverage pragma exists, on the final guard in `split_text`, where the false side is
unreachable because `_split_units` never returns an empty list for non-empty text. Two other
unreachable branches were removed rather than annotated: a redundant backend check in
`create_embedder` and an emptiness guard in the tokenizer's `flush`.

Markdown is linted with markdownlint-cli2. Two rules need accommodating, and the reasons are
worth recording because they will come up again in any MkDocs repository:

- **MD013 line-length** is set to 100, matching the Python line length, with tables and code
  blocks exempt. Wrapping a table row changes the table and wrapping a command breaks it.
- **MD046 code-block-style** is disabled around Material content tabs only, not repo-wide.
  A tab body (`=== "Docker"`) is indented four spaces, which a CommonMark parser reads as an
  indented code block, so the rule then flags every genuine fence in the file. The exception
  sits inline where it applies, so the rule keeps working everywhere else.
- **MD060 table-column-style** is pinned to `compact` rather than left to inference. The
  default infers a style per table and lands on `aligned` for tables whose cells happen to
  line up, which makes `--fix` fail to converge.

---

## 9. Size and performance

| Item | Rough figure |
| --- | --- |
| Added to every page load | ask.js + ask.css + searchbox.js + urls.js about 22 KB, manifest.json about 2 KB |
| `chunks.json` | Comparable to the prose itself, roughly a third of that gzipped |
| `vectors.bin` (int8, 384 dim) | 384 B per chunk, so 3.8 MB for ten thousand chunks; float32 is 15 MB |
| Query encoder (e5-small q8) | 118 MB once, then served from the browser cache |
| BM25 construction in the worker | A few hundred milliseconds for ten thousand chunks |
| Brute-force vector scan | Single-digit to low tens of milliseconds at that size |
| Phase 2 model (0.5B q4) | Several hundred MB, only on click |
| `runtime.vendor: true` | About 160 MB added to `site/`, downloaded once and cached |
| Build-time embedding (CPU, fastembed q8) | Roughly 25 chunks per second; cache hits are free |

---

## 10. Security, privacy and offline use

### What never leaves the machine

- **Document text.** Embeddings are computed by ONNX Runtime on the build machine. The plugin's
  Python code makes no HTTP request of any kind; the only build-time traffic is the embedding
  backend downloading model weights. The index it writes is served from the site's own origin.
- **Reader queries.** The query is embedded in the browser, on WebAssembly or WebGPU. Every
  request the runtime makes is a GET for a static file: there is no POST, no request body and
  no `sendBeacon` anywhere in it, and the Transformers.js bundle contains no telemetry.
- **Generated answers.** The language model runs in the browser too.

### What does leave, by default

Downloads of code and model weights. They carry no content, but they do carry the reader's IP
address, their user agent, and a `Referer` naming the documentation site. On an internal site
the hostname is itself information.

| Host | For | When |
| --- | --- | --- |
| cdn.jsdelivr.net | Transformers.js | first search |
| cdn.jsdelivr.net | ONNX Runtime `.wasm` | first search |
| huggingface.co | embedding model weights | first search |
| esm.run, huggingface.co | WebLLM and the answer model | only if `ai_answer` is used |

The build machine also reaches huggingface.co once per model.

### Closing it completely

```yaml
      runtime:
        vendor: true
```

At build time the plugin copies the library, the ONNX Runtime binaries and the model files into
`site/<output_dir>/vendor/` and rewrites the manifest to point at them, so **the published site
makes no cross-origin request at all** and works with no internet access. Downloads are cached
under `cache_dir`, so only the first build needs the network. The cost is about 160 MB added
to `site/`: 113 MB is the quantised model, 17 MB its tokenizer and 21 MB the ONNX Runtime
binary. With compression enabled on the server, a reader's first search transfers about
90 MB, less than the 140 MB the CDN path costs today, because Hugging Face serves the model
uncompressed.

Vendoring covers retrieval only. With `ai_answer` enabled the answer model still comes from a
CDN, and the build warns about it; point `runtime.webllm_url` and `ai_answer.wasm_model_url` at
self-hosted copies, or leave the feature off.

`runtime.wasm_paths` exists because Transformers.js assigns its own default for the ONNX
Runtime binaries:

```js
M.wasm.wasmPaths || (M.wasm.wasmPaths = `https://cdn.jsdelivr.net/npm/@huggingface/transformers@${version}/dist/`)
```

Self-hosting the library alone therefore leaves that one request going to jsDelivr, on both the
WebAssembly and the WebGPU path. Setting `wasm_paths` is what stops it, and `vendor: true` sets
it for you.

### Referrer

`runtime.no_referrer`, on by default, wraps the worker's `fetch` so cross-origin requests use
`referrerPolicy: "no-referrer"`. That covers the model and wasm downloads, which are made by
Transformers.js rather than by us. It cannot cover the dynamic `import()` of the library
itself, because a module request takes no options, and browsers send at least the origin under
the default `strict-origin-when-cross-origin` policy. Only vendoring removes that one. A
site-wide `Referrer-Policy: no-referrer` header, or `<meta name="referrer" content="no-referrer">`,
would also do it, but that is the site's decision to make and the plugin does not touch it.

### Other things worth knowing

- Under a Content-Security-Policy, a vendored site needs only `worker-src 'self'` and
  `script-src 'self'`. A non-vendored one also needs the CDN in `script-src` and the model
  origin in `connect-src`. Pinning CSP to your own origin is a useful backstop: a
  misconfiguration then fails loudly instead of leaking quietly.
- The plugin is not the whole page. Material for MkDocs loads Google Fonts on every page view,
  which is a larger and far more frequent third-party request than anything here.
  `theme.font: false` stops it.
- `exclude` keeps pages out of the index, though on a static site the real access boundary is
  the hosting layer.

## 11. Known limitations and future work

- `mkdocs build --dirty` only re-renders changed pages, so `on_page_content` sees a subset and
  the index comes out incomplete. The built-in `search` plugin has the same constraint.
- MkDocs' default slugify turns non-ASCII headings into anonymous anchors such as `_1` and `_2`.
  They work as links but read poorly, so a Unicode-preserving slugify is recommended; the example
  site configures `pymdownx.slugs.slugify`.
- At hundreds of thousands of chunks, brute force and an in-memory BM25 stop being reasonable.
  Options: ship a prebuilt inverted index, use an HNSW build such as `hnswlib-wasm`, and SIMD for
  the int8 scan.
- Phase 2 has no browser-level test, and the wllama path has not been exercised on real hardware.
- Future: a small cross-encoder reranker in ONNX, multi-turn context, and TypeScript with
  esbuild while still shipping plain JS.

---

## 12. Deviations from the original sketch

| Original sketch | This design | Why |
| --- | --- | --- |
| A floating panel in the corner | No UI of our own; extend the theme's search field | One place to search. The plugin is invisible until the reader uses the box they already know |
| Chunk in `on_page_markdown` | Chunk in `on_page_content` (HTML) | Heading `id`s are available, so citations can deep-link |
| Embed with sentence-transformers | fastembed loading **the same ONNX weights as the browser** | No PyTorch, and query and document vectors are guaranteed comparable |
| `index.bin` plus `chunks.json` | Add `manifest.json` | One fetch gives the runtime its config, model metadata and file list |
| float16 vectors considered | int8 symmetric quantisation by default | `Float16Array` support is uneven; int8 is a quarter of the size and accurate enough |
| BM25 left unspecified | Tokenizer spec v1: CJK bigrams plus identifier preservation | Handles CJK and technical prose, with one implementation shared by both sides |
| Weighted score fusion | Reciprocal rank fusion by default | Measured: score fusion ranked below semantic search alone, because cosine scores occupy a narrow band |
| Flat options (`model`, `chunk_size`, ...) | Grouped into `embedding`, `chunking`, `search`, `ai_answer`, `ui`, `runtime` | Room to grow without name collisions |
