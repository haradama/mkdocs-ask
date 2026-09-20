import assert from "node:assert/strict";
import { test } from "node:test";
import { THEME_ADAPTERS, detectAdapter } from "../../src/mkdocs_ask/assets/searchbox.js";

/** A stand-in for document.querySelector, so adapter resolution is testable without a DOM. */
function fakeSelect(present) {
  return (selector) => (present.includes(selector) ? { selector } : null);
}

const MATERIAL_INPUT = '[data-md-component="search-query"]';
const MATERIAL_LIST = ".md-search-result__list";

test("auto detection prefers Material and resolves its result list and toggle", () => {
  const found = detectAdapter(
    fakeSelect([MATERIAL_INPUT, MATERIAL_LIST, '[data-md-toggle="search"]']),
    {},
  );
  assert.equal(found.name, "material");
  assert.equal(found.input.selector, MATERIAL_INPUT);
  assert.equal(found.results.selector, MATERIAL_LIST);
  // Kept as a string so it can be re-queried after the theme re-renders its list.
  assert.equal(found.resultsSelector, MATERIAL_LIST);
  assert.equal(found.dismiss.selector, '[data-md-toggle="search"]');
});

test("auto detection falls through to the next theme when Material is absent", () => {
  const found = detectAdapter(fakeSelect(["#mkdocs-search-query", "#mkdocs-search-results"]), {});
  assert.equal(found.name, "mkdocs");
  assert.equal(found.results.selector, "#mkdocs-search-results");
  assert.equal(found.dismiss, null);
});

test("a theme with an input but no known result list still attaches", () => {
  const found = detectAdapter(fakeSelect(['input[type="search"]']), {});
  assert.equal(found.name, "generic");
  assert.equal(found.results, null);
  assert.equal(found.resultsSelector, null);
});

test("the readthedocs sidebar field attaches with no result list", () => {
  // Ordinary readthedocs pages have only a sidebar input; the dropdown lives on search.html.
  const found = detectAdapter(fakeSelect(['input[name="q"]']), {});
  assert.equal(found.name, "readthedocs");
  assert.equal(found.resultsSelector, null);
});

test("detection returns null when the page has no search field", () => {
  assert.equal(detectAdapter(fakeSelect([]), {}), null);
});

test("an explicit selector overrides auto detection", () => {
  const select = fakeSelect([MATERIAL_INPUT, MATERIAL_LIST, ".my-search input", ".my-results"]);
  const found = detectAdapter(select, {
    selector: ".my-search input",
    results_selector: ".my-results",
  });
  assert.equal(found.name, "custom");
  assert.equal(found.input.selector, ".my-search input");
  assert.equal(found.results.selector, ".my-results");
});

test("an explicit selector that matches nothing disables the integration", () => {
  assert.equal(detectAdapter(fakeSelect([MATERIAL_INPUT]), { selector: ".absent" }), null);
});

test("results_selector overrides the adapter's own list selector", () => {
  const found = detectAdapter(fakeSelect([MATERIAL_INPUT, "#custom-list"]), {
    results_selector: "#custom-list",
  });
  assert.equal(found.name, "material");
  assert.equal(found.results.selector, "#custom-list");
});

test("every shipped adapter declares an input selector", () => {
  assert.ok(THEME_ADAPTERS.length >= 3);
  for (const adapter of THEME_ADAPTERS) {
    assert.equal(typeof adapter.name, "string");
    assert.equal(typeof adapter.input, "string");
    assert.ok(adapter.results === null || typeof adapter.results === "string");
  }
});
