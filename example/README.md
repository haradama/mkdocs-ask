# Example site: Kagura Docs

A fictional product documentation site, used to exercise
[mkdocs-ask](../README.md) on something closer to real technical writing than a toy: 23 pages,
around 200 chunks, deep heading hierarchies, reference tables full of error codes, CLI flags,
configuration keys, metric names and environment variables, plus prose that answers questions
without using the reader's words.

## Running it

```bash
cd example
uv run mkdocs serve          # http://127.0.0.1:8000
```

The first build downloads the embedding model, about 118 MB into `.cache/mkdocs-ask`, and
embeds roughly 200 chunks in a few seconds. Later builds reuse the cache and only embed what
changed. There is no separate widget to find: type in the theme's own search box in the header
and ask's semantic matches appear above Material's own results. Try:

| Try asking | Why it is interesting |
| --- | --- |
| `KGR-5070` | A bare identifier with no meaning for an embedding model |
| `--compaction-parallelism` | A flag, kept whole by the tokenizer |
| `storage.min_free_bytes` | One row among ten near-identical configuration tables |
| we are running out of disk space | The section is titled "When the disk fills up" |
| how do I stop the same event being stored twice | The docs only ever say "idempotency key" |
| why does ERR_DISK_FULL happen and how do I recover | An exact code plus an intent |

## What the configuration demonstrates

`mkdocs.yml` is commented throughout. The parts worth copying:

- `exclude` keeps the changelog and the generated API stubs out of the index. Release notes are
  mostly dates and version numbers, and they crowd out useful matches.
- `chunking.include_code: true` is what makes flags and configuration keys findable. Turning it
  off removes most of the exact-match value.
- A Unicode-preserving `toc.slugify`, so citations link to `#when-the-disk-fills-up` rather than
  MkDocs' default `#_7`.
- `ui.strings` relabels the panel without touching the plugin.
- `search_box` puts ask inside Material's own search field, which is its only entry point, so
  the header search returns the theme's keyword results *and* ask's semantic matches in one
  dropdown, with a button that opens the conversation panel. Try typing
  `running out of disk space` in the header box and compare the two halves of the dropdown.
- `ui.preload: focus` starts fetching the query encoder when the reader focuses that field,
  so the model is not downloaded by anyone who never searches.
- A commented `runtime.vendor` block. Turning it on copies the library, the ONNX Runtime
  binaries and the model into `site/`, so the built site makes no cross-origin request at all.
  Useful to try once: `uv run mkdocs build` then grep the built assets for `jsdelivr`.

## Measuring retrieval quality

Guessing whether a change to weights, chunk size or the embedding model helped is how search
quality quietly rots. `scripts/eval.py` scores a golden query set against the built index:

```bash
uv run mkdocs build
uv run python scripts/eval.py
```

It embeds each query with the plugin's own embedder, then scores it with the runtime's own
tokenizer, BM25 and fusion, so the numbers describe what a visitor's browser would really rank.
Targets in `scripts/queries.yml` name a **section**, not just a page, because page-level scoring
flatters everything on a site this size.

The report compares four retrievers on the same queries:

```text
                                                 keyword  semantic  weighted       rrf
exact strings, which keyword search carries          7/7       7/7       7/7       7/7
paraphrases, which semantic search carries           4/8       6/8       7/8       7/8
mixed, where both halves contribute                  3/4       4/4       4/4       4/4
overall                                            14/19     17/19     18/19     18/19
```

Read it as the argument for hybrid retrieval. Keyword search misses half the paraphrases, and
semantic search, though strong here, still trails the fused ranking. The gap would widen on a
larger corpus, where an exact identifier is the only thing separating a hundred similar-looking
sections.

The harness earned its keep during development. The first implementation fused max-normalised
scores, and it scored *below* semantic search alone, because cosine similarities from an
e5-style model all sit between about 0.80 and 0.92, so dividing by the maximum left every
candidate near 1.0 and let BM25 noise decide the order. That is why the default is now
reciprocal rank fusion, which uses only positions. On this corpus `weighted` reaches the same
hit rate with a better mean reciprocal rank; `rrf` is the default because it does not need
retuning when you change embedding model.

One query is marked `known_miss` in `queries.yml`. It is scored and shown but does not fail the
run, because a golden set that always passes measures nothing.

## Layout

```text
example/
|- mkdocs.yml            heavily commented plugin configuration
|- docs/                 23 pages: guides, operations, reference, support
|  +- api/generated/     excluded from the index, to show `exclude` working
+- scripts/
   |- queries.yml        golden queries with section-level targets
   |- eval.py            embeds the queries, then runs eval.mjs
   +- eval.mjs           scores them with the runtime's own retrieval code
```
