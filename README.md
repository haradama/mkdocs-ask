# mkdocs-ask

[![CI](https://github.com/haradama/mkdocs-ask/actions/workflows/ci.yml/badge.svg)](https://github.com/haradama/mkdocs-ask/actions/workflows/ci.yml)
[![Demo](https://img.shields.io/badge/demo-live-6750a4)](https://haradama.github.io/mkdocs-ask/)

Conversational, **fully client-side** search for [MkDocs](https://www.mkdocs.org/).

`mkdocs build` chunks every page by heading and embeds the chunks. In the browser, a Web Worker
runs hybrid retrieval, BM25 keyword matching fused with vector similarity computed by
[Transformers.js](https://huggingface.co/docs/transformers.js) on WebAssembly or WebGPU.

The results appear **inside your theme's own search box**. The plugin adds no launcher, no
corner widget and no second place to type: it extends the field readers already use, and opens
a conversation panel only when they ask it to. Optionally, an on-device small language model
writes an answer grounded in the retrieved sections.

No API server, no vector database, no inference endpoint. The built `site/` directory is the
whole deployment.

**Try it: <https://haradama.github.io/mkdocs-ask/>** — the [example/](example/) site, built and
published by [pages.yml](.github/workflows/pages.yml). Type in the header search box and ask
something the docs never word that way, such as *we are running out of disk space*.

See [DESIGN.md](DESIGN.md) for the architecture, and [example/](example/) for that site's
configuration and its retrieval evaluation harness.

## Quick start

```bash
uv add "mkdocs-ask[fastembed]"      # or: pip install "mkdocs-ask[fastembed]"
```

```yaml
# mkdocs.yml
plugins:
  - search      # required: ask attaches to the field this makes the theme render
  - ask
```

```bash
mkdocs build   # the first run downloads the embedding model, ~118 MB, cached in .cache/mkdocs-ask
```

Open the site and type in the theme's search box. Semantic matches appear above the theme's
own results, with a button that opens the conversation panel.

## Why hybrid retrieval

Semantic search alone loses exact strings, which is most of what a technical question is made of.
Keyword search alone loses the question. The plugin runs both and fuses the scores, so a single
index answers both kinds of query:

| Query | Matched by |
| --- | --- |
| `ERR_QUOTA_EXCEEDED` | keyword, the string appears verbatim |
| `--compaction-parallelism` | keyword, flags survive tokenisation intact |
| "how do I stop the same event being stored twice" | semantic, the page says "idempotency key" |
| "running out of disk space" | semantic, the page is titled "Retention and compaction" |

`example/scripts/eval.py` measures exactly this against the demo site and prints the rank each
mode achieved, so a change to weights or chunking can be judged rather than guessed. On that
corpus, keyword search finds the right section for 14 of 19 questions and semantic search 17,
while the fused ranking finds 18.

The two retrievers are combined with reciprocal rank fusion by default, which uses only the
position of each hit. Combining raw scores instead needs care: BM25 is unbounded while cosine
similarities from an e5-style model sit between about 0.80 and 0.92, so a naive normalisation
lets keyword noise outvote a confident semantic match. Set `search.fusion: weighted` to combine
min-max normalised scores, which reacts to margins rather than order and can rank better once
you have settled on one embedding model.

## How it works

```text
mkdocs build -> chunk by heading -> embed (fastembed, ONNX) -> site/assets/ask/
                                                               {manifest,chunks}.json, vectors.bin
browser      -> worker.js: BM25 + query embedding (Transformers.js) -> fused top-k -> citations
                ^ optional, lazy: generator.js (WebLLM on WebGPU, wllama on WASM)
```

- **The same weights on both sides.** The build uses the exact ONNX repository the browser loads,
  `Xenova/multilingual-e5-small` by default, so query and document vectors are comparable.
- **CJK-aware tokenizer.** Character bigrams for CJK text, with identifiers such as
  `getUserById` and `--flag-name` kept whole for exact matching.
- **Nothing heavy until needed.** A page load adds a few KB. The query encoder downloads when the
  panel opens, the language model only when the reader asks for an AI answer.
- **Keyword-only fallback.** With no embedding backend installed, or if the model cannot be
  fetched in the browser, the plugin degrades to BM25 search instead of breaking.
- **It can make no external requests at all.** One setting vendors every dependency into the
  site. See below.
- **One search box.** Rather than adding a second place to type, the plugin extends the field
  readers already use.
- **Incremental builds.** Embeddings are cached by content hash, so only changed chunks are
  recomputed.

## How it sits in the theme

The built-in `search` plugin is untouched. It keeps its input, its index and its own result
list. The plugin inserts one block as a sibling immediately *before* that list, never inside
any theme-owned node, so a theme that rewrites its results on every keystroke cannot wipe it
and the theme's keyboard navigation still works:

```text
 [data-md-component="search-result"]        Material
   |- .md-search-result__meta               theme's "N matching documents"
   |- section.mkask-sb                      semantic matches + "Ask the docs" button
   +- .md-search-result__list               theme's own results, untouched
```

Material, the mkdocs theme and readthedocs are detected automatically; any other theme is
reached with `selector` and `results_selector`. If no search field exists on the page, the
plugin stays silent rather than adding a button nobody asked for, and the build warns when no
search plugin is enabled.

Two modes:

```yaml
      search_box:
        mode: augment      # search as you type, list the top hits
        # mode: handoff    # no search at all, only a button that opens the panel
```

`handoff` downloads nothing until the reader opens the panel, which is the option for a site
that does not want typing in the search box to pull a 118 MB model. Either way the block shares
the panel's worker, so there is one index fetch and one query encoder per page.

## Privacy, and making the site self-contained

Document text and reader queries never leave the machine they are on. Embeddings are computed
locally at build time, queries are embedded in the reader's browser, and the runtime makes no
POST request of any kind.

What does leave, by default, is downloads: the Transformers.js bundle and the ONNX Runtime
binaries from jsDelivr, and the model weights from huggingface.co. Those carry no content, but
they do carry the reader's IP address and a `Referer` naming your site, which is usually
unacceptable for internal documentation.

```yaml
      runtime:
        vendor: true
```

That copies the library, the ONNX Runtime binaries and the model into
`site/<output_dir>/vendor/` at build time and rewrites the manifest to point at them, so the
published site makes **no cross-origin request at all** and works with no internet access.
Downloads are cached, so only the first build needs the network. It adds about 160 MB to
`site/`, almost all of it the model.

Two things it does not cover:

- With `ai_answer` enabled, the answer model still comes from a CDN. The build warns; set
  `runtime.webllm_url` and `ai_answer.wasm_model_url` to self-hosted copies, or leave it off.
- The plugin is not the whole page. Material for MkDocs loads Google Fonts on every page view,
  which is a far more frequent third-party request than anything here. `theme.font: false`
  stops that.

Without vendoring, `runtime.no_referrer` (on by default) still strips the `Referer` from the
model and wasm downloads. It cannot strip it from the dynamic `import()` of the library, since
module requests take no options, so the CDN still learns your origin. If you are hosting the
library yourself rather than vendoring, set `runtime.wasm_paths` too: Transformers.js points
the ONNX Runtime binaries at jsDelivr on its own, so self-hosting the library alone leaves that
request in place.

## Configuration

Every option, with its default:

```yaml
plugins:
  - ask:
      language: en
      output_dir: assets/ask
      exclude: []                      # fnmatch patterns on the page's src_uri
      embedding:
        model: multilingual-e5-small   # registry name, or a Hugging Face id plus dim
        backend: auto                  # auto | fastembed | sentence-transformers | none
        dtype: int8                    # shipped vectors: int8 | float32
        browser_dtype: q8              # Transformers.js dtype: q8 | fp16 | fp32 | q4
        build_dtype: auto              # ONNX file used at build time: auto | fp32 | q8
        cache_dir: .cache/mkdocs-ask
        batch_size: 32
        include_breadcrumbs: true
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
        model: Qwen2.5-0.5B-Instruct-q4f16_1-MLC
        backend: auto                  # auto | webgpu | wasm
        wasm_model_url: null           # GGUF URL for the WASM path
        max_tokens: 512
        temperature: 0.2
        context_chunks: 5
        system_prompt: null
      ui:                              # the panel the search field opens; no launcher exists
        title: Ask the docs
        placeholder: Ask a question about these docs...
        preload: focus                 # focus | idle | query
        strings: {}                    # override any UI string, e.g. to localise
      search_box:                      # how ask extends the theme's search field
        mode: augment                  # augment | handoff
        selector: auto                 # auto-detect the theme, or a CSS selector
        results_selector: auto         # the block goes just before this element
        min_chars: 3
        debounce_ms: 200
        max_results: 3
      runtime:
        transformers_url: https://cdn.jsdelivr.net/npm/@huggingface/transformers@3
        webllm_url: https://esm.run/@mlc-ai/web-llm
        wllama_url: https://cdn.jsdelivr.net/npm/@wllama/wllama@2/esm/index.js
        model_base_url: null           # self-host models: <base>/<onnx_id>/...
        wasm_paths: null               # ONNX Runtime binaries; set when self-hosting by hand
        vendor: false                  # copy every dependency into site/, no external requests
        no_referrer: true              # strip Referer from the runtime's cross-origin fetches
        device: auto                   # auto | webgpu | wasm
```

Registry models: `multilingual-e5-small` (default), `multilingual-e5-base`,
`bge-small-en-v1.5`, `all-MiniLM-L6-v2`.

### Localising the panel

The runtime ships English strings only. Override them per site:

```yaml
      ui:
        title: Ask the docs
        strings:
          found: "Here is what I found:"
          none: "Nothing matched. Try different words."
          sources: "Sources"
          searchboxHeading: "Semantic matches"
          searchboxAsk: "Ask the docs"
```

## Development

```bash
uv sync --all-extras
uv run pytest                   # chunker, indexer, a real mkdocs build
uv run pytest --cov             # ... gated at 100%, statements and branches
npm test                        # tokenizer, BM25, fusion, search-box DOM (node --test)
npm run test:coverage           # ... gated at 100% for the three testable modules
uv run ruff check src tests && uv run mypy src
npm run lint:md                 # Markdown; npm run lint:md:fix applies what it can
cd example && uv run mkdocs serve
```

`mkdocs serve` does not re-import the plugin's Python code, so restart it after editing
anything under `src/`. Otherwise MkDocs validates a new `mkdocs.yml` against the version of
the plugin it loaded at startup, and reports the mismatch as a configuration error.

CI runs the same commands on every push. To run it locally, install
[act](https://github.com/nektos/act) and use `act push`; `.actrc` in the repository supplies
the runner image.

## Status

Alpha. Retrieval is implemented and covered end to end by tests and by the example evaluation
harness. Coverage is gated at 100% for the Python package and for the three browser modules
that can run under Node; `ask.js`, `worker.js` and the two model adapters in `generator.js`
need a real browser and are verified by building and serving the example site instead. The AI
answer path is experimental and has no automated browser test.

## License

MIT
