/**
 * DOM-level tests for the theme search-field integration, against markup copied from what
 * Material for MkDocs actually renders. This is the most fragile part of the plugin: it lives
 * inside someone else's DOM, next to a list that theme rewrites on every keystroke.
 */

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { JSDOM } from "jsdom";

import { attachSearchBox } from "../../src/mkdocs_ask/assets/searchbox.js";

// Trimmed from example/site/index.html, keeping the structure the adapter relies on.
const MATERIAL_HTML = `
<input id="__search" type="checkbox" data-md-toggle="search" checked>
<div class="md-search" data-md-component="search" role="dialog">
  <form class="md-search__form">
    <input type="text" class="md-search__input" name="query" data-md-component="search-query">
  </form>
  <div class="md-search__output">
    <div class="md-search-result" data-md-component="search-result">
      <div class="md-search-result__meta">Type to start searching</div>
      <ol class="md-search-result__list" role="presentation"></ol>
    </div>
  </div>
</div>`;

const RESULTS = [
  {
    id: 3,
    url: "guide/retention/#when-the-disk-fills-up",
    heading: "When the disk fills up",
    breadcrumbs: ["Retention and compaction", "When the disk fills up"],
    snippet: "Kagura refuses writes at storage.min_free_bytes...",
  },
  {
    id: 9,
    url: "troubleshooting/#running-out-of-disk-space",
    heading: "Running out of disk space",
    breadcrumbs: ["Troubleshooting", "Running out of disk space"],
    snippet: "Writes stop at storage.min_free_bytes...",
  },
];

const STRINGS = {
  loading: "Searching...",
  searchboxHeading: "Semantic matches",
  searchboxHandoff: "Not finding it?",
  searchboxAsk: "Ask the docs",
  searchboxEmpty: "No semantic matches for this wording.",
};

function setup({
  mode = "augment",
  html = MATERIAL_HTML,
  search,
  preload = "focus",
  ...overrides
} = {}) {
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`);
  global.window = dom.window;
  global.document = dom.window.document;
  global.Event = dom.window.Event;

  const opened = [];
  let warmups = 0;
  const manifest = {
    ui: { preload },
    search_box: {
      mode,
      selector: "auto",
      results_selector: "auto",
      min_chars: 3,
      debounce_ms: 0,
      max_results: 3,
      ...overrides,
    },
  };
  const handle = attachSearchBox(manifest, {
    strings: STRINGS,
    search: search || (async () => ({ results: RESULTS, mode: "hybrid" })),
    open: (q) => opened.push(q),
    warmup: () => {
      warmups += 1;
    },
    resolveUrl: (u) => `https://docs.example.com/${u}`,
  });
  return { dom, document: dom.window.document, handle, opened, warmups: () => warmups };
}

function focusField(document, selector = "[data-md-component=search-query]") {
  document
    .querySelector(selector)
    .dispatchEvent(new document.defaultView.Event("focus", { bubbles: true }));
}

/** The listener is debounced and the search is async, so let both settle. */
const settle = () => new Promise((r) => setTimeout(r, 5));

function type(document, value) {
  const input = document.querySelector("[data-md-component=search-query]");
  input.value = value;
  input.dispatchEvent(new document.defaultView.Event("input", { bubbles: true }));
  return settle();
}

afterEach(() => {
  delete global.window;
  delete global.document;
  delete global.Event;
});

test("the block is inserted just above the theme's own result list", async () => {
  const { document } = setup();
  await type(document, "disk full");

  const block = document.querySelector(".mkask-sb");
  const list = document.querySelector(".md-search-result__list");
  assert.ok(block, "block was inserted");
  assert.equal(block.nextElementSibling, list, "sits immediately before the theme's list");
  assert.equal(block.parentElement, document.querySelector("[data-md-component=search-result]"));
  assert.equal(list.children.length, 0, "the theme's own list is left untouched");
});

test("results render with deep links, breadcrumbs and snippets", async () => {
  const { document } = setup();
  await type(document, "disk full");

  const items = [...document.querySelectorAll(".mkask-sb__list li")];
  assert.equal(items.length, 2);
  const link = items[0].querySelector("a");
  assert.equal(link.getAttribute("href"), "https://docs.example.com/guide/retention/#when-the-disk-fills-up");
  assert.equal(link.querySelector("strong").textContent, "When the disk fills up");
  assert.equal(items[0].querySelector(".mkask-sb__crumbs").textContent, "Retention and compaction");
  assert.match(items[0].querySelector(".mkask-sb__snippet").textContent, /min_free_bytes/);
});

test("queries shorter than min_chars are ignored, and clearing hides the block", async () => {
  let calls = 0;
  const { document } = setup({
    search: async () => {
      calls += 1;
      return { results: RESULTS, mode: "hybrid" };
    },
  });

  await type(document, "di");
  assert.equal(calls, 0, "no search below min_chars");
  assert.equal(document.querySelector(".mkask-sb"), null);

  await type(document, "disk full");
  assert.equal(calls, 1);
  assert.equal(document.querySelector(".mkask-sb").hidden, false);

  await type(document, "");
  assert.equal(document.querySelector(".mkask-sb").hidden, true);
  assert.equal(document.querySelectorAll(".mkask-sb__list li").length, 0);
});

test("a stale in-flight response cannot overwrite a newer one", async () => {
  const pending = [];
  const { document } = setup({
    search: (query) =>
      new Promise((resolve) => pending.push({ query, resolve })),
  });

  await type(document, "first query");
  await type(document, "second query");
  assert.equal(pending.length, 2);

  // Resolve them out of order: the slow first request lands after the second.
  pending[1].resolve({ results: [RESULTS[1]], mode: "hybrid" });
  await settle();
  pending[0].resolve({ results: [RESULTS[0]], mode: "hybrid" });
  await settle();

  const headings = [...document.querySelectorAll(".mkask-sb__list strong")].map((n) => n.textContent);
  assert.deepEqual(headings, ["Running out of disk space"], "the newer query's results survive");
});

test("the block is re-inserted after the theme replaces its result list", async () => {
  const { document } = setup();
  await type(document, "disk full");
  assert.ok(document.querySelector(".mkask-sb"));

  // Material rebuilds this subtree as the reader types; the old list element is discarded.
  const wrapper = document.querySelector("[data-md-component=search-result]");
  wrapper.innerHTML = '<div class="md-search-result__meta">2 matches</div><ol class="md-search-result__list"></ol>';
  assert.equal(document.querySelector(".mkask-sb"), null, "the theme wiped our block");

  await type(document, "disk full again");
  const block = document.querySelector(".mkask-sb");
  assert.ok(block, "re-inserted on the next update");
  assert.equal(block.nextElementSibling, document.querySelector(".md-search-result__list"));
});

test("the ask button hands the query over and closes the theme overlay", async () => {
  const { document, opened } = setup();
  await type(document, "how do I stop duplicate events");

  const button = document.querySelector(".mkask-sb__ask");
  assert.match(button.textContent, /^Ask the docs: how do I stop duplicate/);
  button.dispatchEvent(new document.defaultView.MouseEvent("click", { bubbles: true }));

  assert.deepEqual(opened, ["how do I stop duplicate events"]);
  assert.equal(document.querySelector("[data-md-toggle=search]").checked, false);
});

test("handoff mode offers the button without running any search", async () => {
  let calls = 0;
  const { document } = setup({
    mode: "handoff",
    search: async () => {
      calls += 1;
      return { results: RESULTS, mode: "hybrid" };
    },
  });
  await type(document, "disk full");

  assert.equal(calls, 0, "no model download is triggered by typing");
  assert.equal(document.querySelector(".mkask-sb__title").textContent, "Not finding it?");
  assert.equal(document.querySelectorAll(".mkask-sb__list li").length, 0);
  assert.ok(document.querySelector(".mkask-sb__ask"));
});

test("an empty result set shows the empty message", async () => {
  const { document } = setup({ search: async () => ({ results: [], mode: "keyword" }) });
  await type(document, "no such thing");

  const status = document.querySelector(".mkask-sb__status");
  assert.equal(status.hidden, false);
  assert.equal(status.textContent, STRINGS.searchboxEmpty);
});

test("a failing search reports the error instead of throwing", async () => {
  const { document } = setup({
    search: async () => {
      throw new Error("worker unavailable");
    },
  });
  await type(document, "disk full");

  assert.equal(document.querySelector(".mkask-sb__status").textContent, "worker unavailable");
});

test("a theme with no result list falls back to appending near the input", async () => {
  const { document } = setup({
    html: '<form><input type="search" id="q"></form>',
  });
  await type2(document, "disk full");

  const block = document.querySelector(".mkask-sb");
  assert.ok(block);
  assert.equal(block.parentElement.tagName, "FORM");
});

async function type2(document, value) {
  const input = document.querySelector("input");
  input.value = value;
  input.dispatchEvent(new document.defaultView.Event("input", { bubbles: true }));
  await settle();
}

test("focusing the field preloads the encoder once, before anything is typed", async () => {
  const { document, warmups } = setup({ preload: "focus" });
  focusField(document);
  focusField(document);
  assert.equal(warmups(), 1, "listener is once-only");
  assert.equal(document.querySelector(".mkask-sb"), null, "focus alone shows nothing");
});

test("handoff mode never preloads on focus, whatever ui.preload says", async () => {
  const { document, warmups } = setup({ mode: "handoff", preload: "focus" });
  focusField(document);
  assert.equal(warmups(), 0, "handoff exists precisely to avoid the download");
});

test("preload: query holds the encoder back until a query is made", async () => {
  const { document, warmups } = setup({ preload: "query" });
  focusField(document);
  assert.equal(warmups(), 0);
});

test("detach removes the block and stops listening", async () => {
  const { document, handle } = setup();
  await type(document, "disk full");
  assert.ok(document.querySelector(".mkask-sb"));

  handle.detach();
  assert.equal(document.querySelector(".mkask-sb"), null);

  await type(document, "another query");
  assert.equal(document.querySelector(".mkask-sb"), null, "no longer reacting to input");
});

test("a page with no search field attaches nothing and says why", () => {
  // The plugin has no launcher of its own, so this is the case where it is entirely inert.
  const warnings = [];
  const realWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(" "));
  try {
    const { document, handle } = setup({ html: "<p>no search on this page</p>" });
    assert.equal(handle, null);
    assert.equal(document.querySelector(".mkask-sb"), null);
    assert.match(warnings.join(" "), /no search field found/);
    assert.match(warnings.join(" "), /search_box.selector/);
  } finally {
    console.warn = realWarn;
  }
});

test("a long query is truncated on the button but handed over in full", async () => {
  const { document, opened } = setup();
  const long = "why does the ingest path return ERR_BACKPRESSURE under sustained load";
  await type(document, long);

  const label = document.querySelector(".mkask-sb__ask").textContent;
  assert.ok(label.length < long.length, "the button does not stretch the dropdown");
  assert.ok(label.endsWith("\u2026"));

  document
    .querySelector(".mkask-sb__ask")
    .dispatchEvent(new document.defaultView.MouseEvent("click", { bubbles: true }));
  assert.deepEqual(opened, [long], "the panel still receives the whole question");
});

test("a stale failure cannot overwrite a newer query's results", async () => {
  const pending = [];
  const { document } = setup({
    search: (query) => new Promise((resolve, reject) => pending.push({ query, resolve, reject })),
  });

  await type(document, "first query");
  await type(document, "second query");
  pending[1].resolve({ results: [RESULTS[0]], mode: "hybrid" });
  await settle();
  pending[0].reject(new Error("the older request failed"));
  await settle();

  assert.equal(document.querySelector(".mkask-sb__status").hidden, true);
  assert.equal(document.querySelectorAll(".mkask-sb__list li").length, 1);
  assert.match(document.querySelector(".mkask-sb__list strong").textContent, /disk fills up/);
});
