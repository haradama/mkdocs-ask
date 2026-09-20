import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BM25,
  fuse,
  makeSnippet,
  tokenize,
  topKDot,
} from "../../src/mkdocs_ask/assets/search-core.js";

// CJK literals are written as escapes so the repository stays ASCII-only.
const NINSHOU = "\u8a8d\u8a3c"; // two Han characters meaning "authentication"
const HOUSHIKI = "\u65b9\u5f0f"; // two Han characters meaning "method"
const KAGI = "\u9375"; // a single Han character meaning "key"

test("tokenize keeps identifiers and adds sub-tokens", () => {
  const t = tokenize("Call getUserById or foo.bar.baz with --experimental-xxx v1.2.3");
  assert.ok(t.includes("getuserbyid"));
  assert.ok(t.includes("foo.bar.baz"));
  assert.ok(t.includes("bar"));
  assert.ok(t.includes("experimental-xxx"));
  assert.ok(t.includes("experimental"));
  assert.ok(t.includes("v1.2.3"));
  assert.ok(!t.includes("--experimental-xxx"));
});

test("tokenize emits overlapping CJK bigrams", () => {
  const t = tokenize(NINSHOU + HOUSHIKI);
  assert.deepEqual(t, [
    NINSHOU,
    NINSHOU[1] + HOUSHIKI[0],
    HOUSHIKI,
  ]);
});

test("tokenize splits CJK from latin in a mixed run and folds fullwidth forms", () => {
  const t = tokenize(NINSHOU + "OAuth 2.0");
  assert.ok(t.includes(NINSHOU));
  assert.ok(t.includes("oauth"));
  assert.ok(t.includes("2.0"));
  assert.deepEqual(tokenize(KAGI), [KAGI]); // a lone CJK character emits itself
  assert.deepEqual(tokenize("\uff21\uff22\uff23"), ["abc"]); // NFKC folds fullwidth ABC
});

test("BM25 ranks the document mentioning the query term first", () => {
  const docs = [
    "OAuth 2.0 authentication flow and tokens",
    "API keys are sent in the HTTP header",
    "Configuration file reference for the server",
  ].map(tokenize);
  const idx = new BM25(docs);
  const hits = idx.search(tokenize("api key header"));
  assert.equal(hits[0].id, 1);
  assert.equal(idx.search(tokenize("nothing-here")).length, 0);
});

test("topKDot returns cosine-ordered ids for int8 vectors", () => {
  const dim = 4;
  const vectors = new Int8Array([127, 0, 0, 0, 0, 127, 0, 0, 90, 90, 0, 0]);
  const q = new Float32Array([1, 0, 0, 0]);
  const hits = topKDot(q, vectors, dim, 3, 1 / 127, 2);
  assert.deepEqual(hits.map((h) => h.id), [0, 2]);
  assert.ok(Math.abs(hits[0].score - 1) < 1e-6);
});

test("rrf fusion rewards appearing in both lists and ignores score scale", () => {
  // Raw scales differ wildly: BM25 in the tens, cosine under one. Only the order matters.
  const kw = [{ id: 1, score: 10 }, { id: 2, score: 5 }];
  const sm = [{ id: 2, score: 0.901 }, { id: 3, score: 0.900 }];
  const out = fuse(kw, sm, { keywordWeight: 0.4, semanticWeight: 0.6, rrfK: 60 });
  assert.equal(out[0].id, 2); // 0.4/62 + 0.6/61, the only id both retrievers returned
  assert.deepEqual(out.map((h) => h.id), [2, 3, 1]);
  assert.ok(Math.abs(out[0].score - (0.4 / 62 + 0.6 / 61)) < 1e-12);
  // Multiplying one list's scores by a thousand must not change the ranking.
  const scaled = fuse(kw.map((h) => ({ ...h, score: h.score * 1000 })), sm, { rrfK: 60 });
  assert.deepEqual(scaled.map((h) => h.id), [2, 3, 1]);
});

test("weighted fusion min-max normalises each list before combining", () => {
  const kw = [{ id: 1, score: 10 }, { id: 2, score: 5 }];
  const sm = [{ id: 2, score: 0.9 }, { id: 3, score: 0.45 }];
  const out = fuse(kw, sm, { keywordWeight: 0.4, semanticWeight: 0.6, method: "weighted" });
  // min-max puts id2 at 0 for keyword and 1 for semantic: 0.4*0 + 0.6*1 = 0.6, beating id1's 0.4
  assert.equal(out[0].id, 2);
  assert.ok(Math.abs(out[0].score - 0.6) < 1e-9);
  assert.equal(out.length, 3);
});

test("fusion tolerates an empty retriever", () => {
  const only = [{ id: 7, score: 3 }];
  assert.deepEqual(fuse(only, []).map((h) => h.id), [7]);
  assert.deepEqual(fuse([], only).map((h) => h.id), [7]);
  assert.deepEqual(fuse([], []), []);
});

test("makeSnippet centres on the first hit", () => {
  const text = "a ".repeat(200) + "needle in the haystack " + "b ".repeat(200);
  const s = makeSnippet(text, tokenize("needle"), 60);
  assert.ok(s.includes("needle"));
  assert.ok(s.length <= 62);
  assert.ok(s.startsWith("\u2026"));
});

test("a run made only of punctuation produces no token", () => {
  // RUN_RE accepts ._- so "---" is a run, but it strips to nothing and must be dropped
  // rather than indexed as an empty term.
  assert.deepEqual(tokenize("--- ... ___ -.-"), []);
  assert.deepEqual(tokenize(""), []);
  assert.deepEqual(tokenize(null), []);
  assert.deepEqual(tokenize("   "), []);
});

test("BM25 over an empty corpus does not divide by zero", () => {
  const idx = new BM25([]);
  assert.equal(idx.avgdl, 1);
  assert.deepEqual(idx.search(tokenize("anything")), []);
});

test("weighted fusion handles an empty list and a flat one", () => {
  const opts = { method: "weighted", keywordWeight: 0.4, semanticWeight: 0.6 };
  assert.deepEqual(fuse([], [], opts), []);

  const only = [{ id: 1, score: 5 }];
  assert.deepEqual(fuse(only, [], opts).map((h) => h.id), [1]);

  // Every score identical: min-max has no span, so all candidates rank equally rather than
  // collapsing to zero and disappearing behind the other retriever.
  const flat = [{ id: 1, score: 0.9 }, { id: 2, score: 0.9 }];
  const out = fuse([], flat, opts);
  assert.equal(out.length, 2);
  assert.ok(out.every((h) => Math.abs(h.score - 0.6) < 1e-9));
});

test("makeSnippet returns short text whole, with no ellipsis", () => {
  const s = makeSnippet("A short line.", tokenize("short"), 200);
  assert.equal(s, "A short line.");
  assert.ok(!s.includes("\u2026"));
});

test("makeSnippet falls back to the start when nothing matches", () => {
  const text = "word ".repeat(200);
  const s = makeSnippet(text, tokenize("absent"), 50);
  assert.ok(s.startsWith("word"), "no leading ellipsis when starting at the beginning");
  assert.ok(s.endsWith("\u2026"), "trailing ellipsis because the text continues");
});

test("makeSnippet ignores one-character query tokens when choosing where to centre", () => {
  // A single letter would match almost immediately and drag the excerpt to the start.
  const text = "a ".repeat(100) + "needle here " + "b ".repeat(100);
  const s = makeSnippet(text, ["a", "needle"], 60);
  assert.ok(s.includes("needle"));
});

test("makeSnippet centres on the earliest matching token, not the first one listed", () => {
  const text = "alpha " + "x ".repeat(100) + "omega " + "y ".repeat(100);
  const s = makeSnippet(text, ["omega", "alpha"], 40);
  assert.ok(s.includes("alpha"), "the earlier occurrence wins regardless of token order");
});

test("BM25 orders several matches by score, shorter documents winning on length", () => {
  const docs = [
    "authentication",
    "authentication authentication and more filler words here to lengthen this document",
    "unrelated content entirely",
  ].map(tokenize);
  const hits = new BM25(docs).search(tokenize("authentication"));

  assert.equal(hits.length, 2, "the unrelated document does not appear at all");
  assert.deepEqual(hits.map((h) => h.id), [0, 1]);
  assert.ok(hits[0].score > hits[1].score, "twice the term in ten times the text scores lower");
});

test("makeSnippet drops the trailing ellipsis when the excerpt reaches the end", () => {
  const text = "x ".repeat(100) + "final needle";
  const s = makeSnippet(text, tokenize("needle"), 40);
  assert.ok(s.includes("needle"));
  assert.ok(s.startsWith("\u2026"), "text was cut at the front");
  assert.ok(!s.endsWith("\u2026"), "but not at the back, because there is nothing after");
});
