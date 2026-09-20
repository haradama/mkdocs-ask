/**
 * mkdocs-ask: pure retrieval primitives, no DOM and no network.
 *
 * Tokenizer spec v1 (keep in sync with DESIGN.md):
 *   1. NFKC-normalise and lower-case.
 *   2. Take maximal runs of [letters, digits, "_", ".", "-"].
 *   3. Split each run into CJK / non-CJK segments.
 *   4. Non-CJK segment: trim leading/trailing [._-], emit whole token, plus sub-tokens
 *      split on [._-] (length >= 2) so `foo.bar.baz` also matches `bar`.
 *   5. CJK segment (Han/Hiragana/Katakana/Hangul, plus U+30FC prolonged sound mark and
 *      U+3005 iteration mark): overlapping character bigrams.
 *      (a single character emits itself).
 */

const CJK_RE =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\u30FC\u3005]/u;
const RUN_RE = /[\p{L}\p{N}_.\-]+/gu;

export function tokenize(text) {
  const tokens = [];
  if (!text) return tokens;
  const norm = String(text).normalize("NFKC").toLowerCase();
  for (const match of norm.matchAll(RUN_RE)) {
    let seg = "";
    let segIsCjk = null;
    // Both call sites below only fire once `seg` holds at least one character, and a run
    // matched by RUN_RE is never empty, so `flush` needs no emptiness guard of its own.
    const flush = () => {
      if (segIsCjk) pushCjk(seg, tokens);
      else pushWord(seg, tokens);
      seg = "";
    };
    for (const ch of match[0]) {
      const isCjk = CJK_RE.test(ch);
      if (segIsCjk !== null && isCjk !== segIsCjk) flush();
      segIsCjk = isCjk;
      seg += ch;
    }
    flush();
  }
  return tokens;
}

function pushWord(word, out) {
  const w = word.replace(/^[._\-]+|[._\-]+$/g, "");
  if (!w) return;
  out.push(w);
  if (/[._\-]/.test(w)) {
    for (const part of w.split(/[._\-]+/)) if (part.length >= 2) out.push(part);
  }
}

function pushCjk(segment, out) {
  const chars = Array.from(segment);
  if (chars.length === 1) {
    out.push(chars[0]);
    return;
  }
  for (let i = 0; i + 1 < chars.length; i++) out.push(chars[i] + chars[i + 1]);
}

/** Okapi BM25 over pre-tokenised documents. */
export class BM25 {
  constructor(docs, { k1 = 1.2, b = 0.75 } = {}) {
    this.k1 = k1;
    this.b = b;
    this.n = docs.length;
    this.postings = new Map(); // term -> { ids: number[], tfs: number[] }
    this.docLen = new Float32Array(this.n);
    let total = 0;
    docs.forEach((tokens, id) => {
      this.docLen[id] = tokens.length;
      total += tokens.length;
      const counts = new Map();
      for (const t of tokens) counts.set(t, (counts.get(t) || 0) + 1);
      for (const [t, c] of counts) {
        let p = this.postings.get(t);
        if (!p) {
          p = { ids: [], tfs: [] };
          this.postings.set(t, p);
        }
        p.ids.push(id);
        p.tfs.push(c);
      }
    });
    this.avgdl = this.n ? total / this.n : 1;
  }

  search(queryTokens, limit = 50) {
    const scores = new Map();
    for (const term of new Set(queryTokens)) {
      const p = this.postings.get(term);
      if (!p) continue;
      const df = p.ids.length;
      const idf = Math.log(1 + (this.n - df + 0.5) / (df + 0.5));
      for (let i = 0; i < p.ids.length; i++) {
        const id = p.ids[i];
        const tf = p.tfs[i];
        const denom = tf + this.k1 * (1 - this.b + (this.b * this.docLen[id]) / this.avgdl);
        scores.set(id, (scores.get(id) || 0) + (idf * tf * (this.k1 + 1)) / denom);
      }
    }
    return [...scores]
      .map(([id, score]) => ({ id, score }))
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  }
}

/**
 * Brute-force dot product of a float32 query against a flat (count x dim) matrix.
 * `vectors` is an Int8Array (with `scale`) or a Float32Array. Both sides are unit
 * vectors, so the result is cosine similarity.
 */
export function topKDot(query, vectors, dim, count, scale, k) {
  const scores = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    let s = 0;
    const off = i * dim;
    for (let j = 0; j < dim; j++) s += query[j] * vectors[off + j];
    scores[i] = s * scale;
  }
  const ids = new Array(count);
  for (let i = 0; i < count; i++) ids[i] = i;
  ids.sort((a, b) => scores[b] - scores[a]);
  return ids.slice(0, k).map((id) => ({ id, score: scores[id] }));
}

/**
 * Combine the keyword and vector rankings into one list.
 *
 * `rrf` (reciprocal rank fusion) is the default and uses only the position of each hit, so a
 * retriever whose raw scores are compressed into a narrow band cannot be drowned out by one
 * whose scores are spread wide. Cosine similarities from an e5-style model sit between about
 * 0.80 and 0.92, while BM25 is unbounded, which is exactly that situation.
 *
 * `weighted` combines min-max normalised scores instead. It reacts to *how much* better a hit
 * is rather than only to its rank, which helps when one retriever is confidently right, but it
 * is sensitive to score distributions and needs retuning when the model changes.
 *
 * Both return `{id, keyword, semantic, score}` sorted by descending score, where `keyword` and
 * `semantic` are that retriever's contribution for inspection.
 */
export function fuse(keyword, semantic, options = {}) {
  const {
    keywordWeight = 0.4,
    semanticWeight = 0.6,
    method = "rrf",
    rrfK = 60,
  } = options;
  return method === "weighted"
    ? fuseWeighted(keyword, semantic, keywordWeight, semanticWeight)
    : fuseRRF(keyword, semantic, keywordWeight, semanticWeight, rrfK);
}

function fuseRRF(keyword, semantic, keywordWeight, semanticWeight, k) {
  const merged = new Map();
  const contribute = (hits, weight, field) => {
    hits.forEach((hit, rank) => {
      const entry =
        merged.get(hit.id) || { id: hit.id, keyword: 0, semantic: 0, score: 0 };
      entry[field] = weight / (k + rank + 1);
      merged.set(hit.id, entry);
    });
  };
  contribute(keyword, keywordWeight, "keyword");
  contribute(semantic, semanticWeight, "semantic");
  for (const entry of merged.values()) entry.score = entry.keyword + entry.semantic;
  return [...merged.values()].sort((a, b) => b.score - a.score);
}

function fuseWeighted(keyword, semantic, keywordWeight, semanticWeight) {
  const kw = normaliseMinMax(keyword);
  const sm = normaliseMinMax(semantic);
  const ids = new Set([...kw.keys(), ...sm.keys()]);
  return [...ids]
    .map((id) => {
      const k = kw.get(id) || 0;
      const s = sm.get(id) || 0;
      return {
        id,
        keyword: keywordWeight * k,
        semantic: semanticWeight * s,
        score: keywordWeight * k + semanticWeight * s,
      };
    })
    .sort((a, b) => b.score - a.score);
}

/**
 * Rescale a hit list onto 0..1. Min-max rather than divide-by-max, because cosine scores from
 * a sentence embedding model occupy a narrow band well above zero and dividing by the maximum
 * leaves every candidate near 1, erasing the ranking the retriever just produced.
 */
function normaliseMinMax(hits) {
  const out = new Map();
  if (!hits.length) return out;
  const scores = hits.map((h) => h.score);
  const max = Math.max(...scores);
  const min = Math.min(...scores);
  const span = max - min;
  for (const h of hits) out.set(h.id, span > 0 ? (h.score - min) / span : 1);
  return out;
}

/** Short excerpt around the first query-token hit (or the beginning of the text). */
export function makeSnippet(text, tokens, maxChars = 200) {
  const flat = String(text).replace(/\s+/g, " ").trim();
  const lower = flat.normalize("NFKC").toLowerCase();
  let pos = -1;
  for (const t of tokens) {
    if (t.length < 2) continue;
    const i = lower.indexOf(t);
    if (i >= 0 && (pos < 0 || i < pos)) pos = i;
  }
  if (flat.length <= maxChars) return flat;
  const start = pos < 0 ? 0 : Math.max(0, pos - Math.floor(maxChars / 3));
  const end = Math.min(flat.length, start + maxChars);
  const ellipsis = "\u2026";
  return (
    (start > 0 ? ellipsis : "") +
    flat.slice(start, end).trim() +
    (end < flat.length ? ellipsis : "")
  );
}
