# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-20

First release.

### Added

- Build-time indexing: heading-aware chunking of the rendered HTML, so every chunk carries a
  deep link, and document embeddings computed with the same ONNX weights the browser loads.
- In-browser hybrid retrieval: BM25 over a CJK-aware tokenizer, cosine similarity over int8
  vectors, fused with reciprocal rank fusion by default or a weighted sum on request.
- Integration with the theme's own search field for Material, the mkdocs theme and
  readthedocs, with `augment` and `handoff` modes. The plugin adds no widget of its own.
- A conversation panel, opened from the search field, with an experimental on-device answer
  generator (WebLLM on WebGPU, wllama on WebAssembly).
- `runtime.vendor`, which copies the library, the ONNX Runtime binaries and the model into the
  site so the published site makes no cross-origin request at all.
- `runtime.no_referrer`, on by default, and `runtime.wasm_paths` for self-hosted setups.
- An embedding cache keyed by content hash, so repeat builds only embed what changed.
- An example site with a retrieval evaluation harness that scores a golden query set with the
  runtime's own code.

[Unreleased]: https://github.com/haradama/mkdocs-ask/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/haradama/mkdocs-ask/releases/tag/v0.1.0
