/**
 * mkdocs-ask: search worker (a module worker).
 *
 * Owns the index (chunks, BM25, vectors), the lazily loaded query encoder
 * (Transformers.js, WASM or WebGPU) and, when enabled, the answer generator.
 * The main thread only talks to it through postMessage.
 *
 *   -> { type: "init",   baseUrl }
 *   -> { type: "warmup" }                       preload the query encoder
 *   -> { type: "search", id, query }
 *   -> { type: "answer", id, query, ids }       Phase 2: generate from chunks `ids`
 *   <- { type: "ready", count, semantic, manifest }
 *   <- { type: "status", stage, text, progress? }
 *   <- { type: "results", id, mode, results }
 *   <- { type: "token", id, text } ... { type: "answer_done", id }
 *   <- { type: "error", id?, message }
 */

import { BM25, fuse, makeSnippet, tokenize, topKDot } from "./search-core.js";
import { installNoReferrerFetch, resolveRuntimeUrl } from "./urls.js";

const state = {
  baseUrl: null,
  manifest: null,
  chunks: [],
  tokens: [],
  bm25: null,
  vectors: null, // Int8Array | Float32Array
  extractor: null,
  extractorPromise: null,
  semanticDisabled: false,
  generatorPromise: null,
};

self.onmessage = async (event) => {
  const msg = event.data || {};
  try {
    switch (msg.type) {
      case "init":
        await init(msg.baseUrl);
        break;
      case "warmup":
        ensureExtractor().catch(() => {});
        break;
      case "search":
        await search(msg);
        break;
      case "answer":
        await answer(msg);
        break;
      default:
        throw new Error(`unknown message type: ${msg.type}`);
    }
  } catch (err) {
    post({ type: "error", id: msg.id, message: String(err?.message || err) });
  }
};

function post(message) {
  self.postMessage(message);
}

function status(stage, text, progress) {
  post({ type: "status", stage, text, progress });
}

async function init(baseUrl) {
  state.baseUrl = baseUrl;
  const manifest = await fetchJson(new URL("manifest.json", baseUrl));
  state.manifest = manifest;
  state.chunks = await fetchJson(new URL(manifest.chunks.file, baseUrl));
  state.tokens = state.chunks.map((c) => tokenize(`${c.heading} ${c.text}`));
  state.bm25 = new BM25(state.tokens);

  if (manifest.vectors && manifest.model) {
    const buf = await (await fetch(new URL(manifest.vectors.file, baseUrl))).arrayBuffer();
    state.vectors = manifest.vectors.dtype === "int8" ? new Int8Array(buf) : new Float32Array(buf);
    const expected = manifest.vectors.count * manifest.vectors.dim;
    if (state.vectors.length !== expected) {
      throw new Error(`vectors.bin size mismatch (${state.vectors.length} != ${expected})`);
    }
  }
  post({ type: "ready", count: state.chunks.length, semantic: !!state.vectors, manifest });
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`failed to load ${url}: ${res.status}`);
  return res.json();
}

/** Lazily import Transformers.js and create the feature-extraction pipeline. */
function ensureExtractor() {
  if (state.extractor) return Promise.resolve(state.extractor);
  if (state.extractorPromise) return state.extractorPromise;
  const { manifest } = state;
  state.extractorPromise = (async () => {
    status("model", "Loading query encoder...", 0);
    const rt = manifest.runtime;
    if (rt.no_referrer !== false) installNoReferrerFetch(self);

    const tf = await import(/* @vite-ignore */ resolveRuntimeUrl(rt.transformers_url, state.baseUrl));
    tf.env.allowLocalModels = false;

    // Transformers.js defaults the ONNX Runtime binaries to jsDelivr, so a site that hosts
    // the library itself would still reach out for these unless we say otherwise.
    if (rt.wasm_paths) {
      const onnx = (tf.env.backends.onnx ||= {});
      onnx.wasm = { ...(onnx.wasm || {}), wasmPaths: resolveRuntimeUrl(rt.wasm_paths, state.baseUrl) };
    }
    if (rt.model_base_url) {
      const base = resolveRuntimeUrl(rt.model_base_url, state.baseUrl);
      tf.env.remoteHost = base.replace(/\/?$/, "/");
      tf.env.remotePathTemplate = "{model}/";
    }
    const device = pickDevice(manifest.runtime.device);
    const extractor = await tf.pipeline("feature-extraction", manifest.model.onnx_id, {
      dtype: manifest.model.browser_dtype || "q8",
      device,
      progress_callback: (p) => {
        if (p.status === "progress" && typeof p.progress === "number") {
          status("model", `Loading ${p.file} (${Math.round(p.progress)}%)`, p.progress / 100);
        }
      },
    });
    status("model", `Query encoder ready (${device})`, 1);
    state.extractor = extractor;
    return extractor;
  })().catch((err) => {
    state.extractorPromise = null;
    state.semanticDisabled = true;
    status("model", `Semantic search unavailable: ${err.message}. Falling back to keyword search.`);
    throw err;
  });
  return state.extractorPromise;
}

function pickDevice(pref) {
  if (pref === "webgpu" || pref === "wasm") return pref;
  return typeof navigator !== "undefined" && navigator.gpu ? "webgpu" : "wasm";
}

async function embedQuery(text) {
  const extractor = await ensureExtractor();
  const { model } = state.manifest;
  const out = await extractor(`${model.query_prefix || ""}${text}`, {
    pooling: model.pooling || "mean",
    normalize: true,
  });
  return out.data instanceof Float32Array ? out.data : Float32Array.from(out.data);
}

async function search({ id, query }) {
  const { manifest } = state;
  const cfg = manifest.search;
  const q = String(query || "").trim();
  if (!q) {
    post({ type: "results", id, mode: "keyword", results: [] });
    return;
  }
  const qTokens = tokenize(q);
  const keywordHits = state.bm25.search(qTokens, cfg.candidates);

  let semanticHits = [];
  let mode = "keyword";
  const wantSemantic = state.vectors && !state.semanticDisabled && cfg.semantic_weight > 0;
  if (wantSemantic) {
    try {
      const qv = await embedQuery(q);
      const { dim, count, scale } = manifest.vectors;
      semanticHits = topKDot(qv, state.vectors, dim, count, scale, cfg.candidates);
      mode = cfg.hybrid && cfg.keyword_weight > 0 ? "hybrid" : "semantic";
    } catch {
      mode = "keyword";
    }
  }

  let ranked;
  if (mode === "hybrid") {
    ranked = fuse(keywordHits, semanticHits, {
      keywordWeight: cfg.keyword_weight,
      semanticWeight: cfg.semantic_weight,
      method: cfg.fusion,
      rrfK: cfg.rrf_k,
    });
  } else if (mode === "semantic") {
    ranked = semanticHits;
  } else {
    ranked = keywordHits;
  }

  const results = ranked.slice(0, cfg.top_k).map((hit) => {
    const c = state.chunks[hit.id];
    return {
      id: hit.id,
      score: hit.score,
      url: c.url,
      page_title: c.page_title,
      heading: c.heading,
      breadcrumbs: c.breadcrumbs,
      snippet: makeSnippet(c.text, qTokens, cfg.snippet_chars),
    };
  });
  post({ type: "results", id, mode, results });
}

/** Phase 2: stream an answer grounded in the given chunk ids. */
async function answer({ id, query, ids }) {
  const { manifest } = state;
  if (!manifest.ai_answer?.enabled) throw new Error("ai_answer is disabled in mkdocs.yml");
  if (!state.generatorPromise) {
    state.generatorPromise = import("./generator.js")
      .then((m) =>
        m.createGenerator(
          manifest,
          (text, progress) => status("generator", text, progress),
          state.baseUrl,
        ),
      )
      .catch((err) => {
        state.generatorPromise = null;
        throw err;
      });
  }
  const generator = await state.generatorPromise;
  const contexts = (ids || []).slice(0, manifest.ai_answer.context_chunks).map((i) => state.chunks[i]);
  for await (const token of generator.generate(query, contexts)) {
    post({ type: "token", id, text: token });
  }
  post({ type: "answer_done", id });
}
