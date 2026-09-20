/**
 * Scores the golden queries against a built index using the runtime's own retrieval code,
 * so the numbers describe what a visitor's browser would actually rank.
 *
 * Invoked by eval.py, which supplies the query vectors:
 *   node scripts/eval.mjs <index-dir> <payload.json>
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { BM25, fuse, tokenize, topKDot } from "../../src/mkdocs_ask/assets/search-core.js";

const [indexDir, payloadPath] = process.argv.slice(2);
const manifest = readJson(join(indexDir, "manifest.json"));
const chunks = readJson(join(indexDir, manifest.chunks.file));
const payload = readJson(payloadPath);
const topK = payload.top_k ?? manifest.search.top_k;
const active = manifest.search.fusion === "weighted" ? "weighted" : "rrf";

const bm25 = new BM25(chunks.map((c) => tokenize(`${c.heading} ${c.text}`)));

let vectors = null;
if (manifest.vectors && Object.keys(payload.vectors).length) {
  const buf = readFileSync(join(indexDir, manifest.vectors.file));
  vectors =
    manifest.vectors.dtype === "int8"
      ? new Int8Array(buf.buffer, buf.byteOffset, buf.length)
      : new Float32Array(buf.buffer, buf.byteOffset, buf.length / 4);
}

const modes = ["keyword", "semantic", "weighted", "rrf"];
const totals = Object.fromEntries(modes.map((m) => [m, 0]));
const rows = [];

for (const query of payload.queries) {
  const qTokens = tokenize(query.q);
  const keyword = bm25.search(qTokens, manifest.search.candidates);
  let semantic = [];
  if (vectors && payload.vectors[query.q]) {
    const qv = Float32Array.from(payload.vectors[query.q]);
    const { dim, count, scale } = manifest.vectors;
    semantic = topKDot(qv, vectors, dim, count, scale, manifest.search.candidates);
  }
  const weights = {
    keywordWeight: manifest.search.keyword_weight,
    semanticWeight: manifest.search.semantic_weight,
    rrfK: manifest.search.rrf_k ?? 60,
  };
  const weighted = fuse(keyword, semantic, { ...weights, method: "weighted" });
  const rrf = fuse(keyword, semantic, { ...weights, method: "rrf" });
  const hybrid = manifest.search.fusion === "weighted" ? weighted : rrf;

  const ranks = {
    keyword: rankOf(keyword, query.expect),
    semantic: rankOf(semantic, query.expect),
    weighted: rankOf(weighted, query.expect),
    rrf: rankOf(rrf, query.expect),
  };
  for (const mode of modes) if (ranks[mode]) totals[mode] += 1;
  rows.push({ ...query, ranks, top: chunks[hybrid[0]?.id]?.url ?? "-" });
}

/** 1-based rank of the first hit matching any expected prefix, or 0 if outside top-k. */
function rankOf(hits, expect) {
  const prefixes = Array.isArray(expect) ? expect : [expect];
  for (let i = 0; i < Math.min(hits.length, topK); i++) {
    const { url } = chunks[hits[i].id];
    if (prefixes.some((p) => url.startsWith(p))) return i + 1;
  }
  return 0;
}

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf-8"));
}

// ---- report ------------------------------------------------------------------------------

const model = manifest.model
  ? `${manifest.model.onnx_id} (${manifest.model.dim}d, ${manifest.vectors.dtype})`
  : "none, keyword only";
console.log(`\nmkdocs-ask retrieval evaluation`);
console.log(`index    ${indexDir}`);
console.log(`chunks   ${chunks.length} from ${manifest.pages} pages`);
console.log(`model    ${model}`);
console.log(
  `weights  keyword ${manifest.search.keyword_weight} / semantic ${manifest.search.semantic_weight}` +
    `, hit = expected page within top ${topK}\n`,
);

const width = Math.max(...rows.map((r) => r.q.length), 40);
let lastGroup = null;
for (const row of rows) {
  if (row.group !== lastGroup) {
    lastGroup = row.group;
    console.log(`  ${row.group}`);
    console.log(`  ${"".padEnd(width + 30, "-")}`);
    console.log(`  ${"query".padEnd(width)}  kw  vec  wgt  rrf   target`);
  }
  console.log(
    `  ${row.q.padEnd(width)} ` +
      `${fmt(row.ranks.keyword)} ${fmt(row.ranks.semantic)} ` +
      `${fmt(row.ranks.weighted)} ${fmt(row.ranks.rrf)}` +
      `   ${row.ranks[active] ? first(row.expect) : `${row.known_miss ? "known miss" : "MISS"}, got ${row.top}`}`,
  );
}

function first(expect) {
  return Array.isArray(expect) ? `${expect[0]} (+${expect.length - 1})` : expect;
}

function fmt(rank) {
  return String(rank || "-").padStart(4);
}

const groups = [...new Set(rows.map((r) => r.group))];
console.log(`\n  hits by group (expected section within top ${topK})\n`);
console.log(`    ${"".padEnd(46)}${modes.map((m) => m.padStart(10)).join("")}`);
for (const group of groups) {
  const inGroup = rows.filter((r) => r.group === group);
  const cells = modes.map((m) =>
    `${inGroup.filter((r) => r.ranks[m]).length}/${inGroup.length}`.padStart(10),
  );
  console.log(`    ${group.padEnd(46)}${cells.join("")}`);
}
const overall = modes.map((m) => `${totals[m]}/${rows.length}`.padStart(10));
console.log(`    ${"overall".padEnd(46)}${overall.join("")}`);
console.log(`\n  mean reciprocal rank\n`);
for (const mode of modes) {
  const mrr = rows.reduce((a, r) => a + (r.ranks[mode] ? 1 / r.ranks[mode] : 0), 0) / rows.length;
  const bar = "#".repeat(Math.round(mrr * 40));
  console.log(`    ${mode.padEnd(10)} ${mrr.toFixed(3)}  ${bar}`);
}
const known = rows.filter((r) => r.known_miss).length;
console.log(
  `\n  active fusion: ${active}. Every method is shown so a change can be judged, not guessed.` +
    (known ? `\n  ${known} query marked known_miss in queries.yml, excluded from the exit status.` : "") +
    "\n",
);

const regressions = rows.filter((r) => !r.ranks[active] && !r.known_miss);
if (regressions.length) {
  console.log("  regressions (not marked known_miss in queries.yml):");
  for (const r of regressions) console.log(`    ${r.q}`);
  console.log();
}
process.exit(regressions.length ? 1 : 0);
