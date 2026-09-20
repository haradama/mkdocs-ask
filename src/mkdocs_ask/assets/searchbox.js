/**
 * mkdocs-ask: integration with the theme's own search field.
 *
 * The built-in `search` plugin keeps full ownership of the input and of its result list. This
 * module inserts one block as a *sibling* immediately before that list, never inside it, so a
 * theme re-rendering its results on every keystroke cannot wipe it and we cannot corrupt the
 * theme's keyboard navigation. If the block is ever detached anyway, the next update re-inserts
 * it.
 *
 * This is the plugin's only entry point: there is no launcher of its own, so if the theme
 * renders no search field the plugin stays silent rather than adding a button nobody asked for.
 */

/**
 * Selectors for the themes we know about, tried in order when `selector` is "auto".
 * Each one was read off the markup those themes actually render, not from their docs.
 *
 * Material nests its list as `.md-search-result__list` inside
 * `[data-md-component="search-result"]`, and drives the overlay from a checkbox.
 * The readthedocs theme has no live dropdown on ordinary pages, only a sidebar field, so it
 * resolves with no result list and gets the handoff button instead; its dedicated search page
 * carries the mkdocs-theme ids and matches the entry above it.
 */
export const THEME_ADAPTERS = [
  {
    name: "material",
    input: '[data-md-component="search-query"]',
    results: ".md-search-result__list",
    dismiss: '[data-md-toggle="search"]',
  },
  { name: "mkdocs", input: "#mkdocs-search-query", results: "#mkdocs-search-results" },
  { name: "readthedocs", input: 'input[name="q"]', results: null },
  { name: "generic", input: 'input[type="search"]', results: null },
];

/**
 * Resolve which adapter to use. `select` is a `document.querySelector`-like function, injected
 * so this stays a pure function and can be unit tested without a DOM.
 *
 * Returns `{name, input, results, resultsSelector, dismiss}`, or null when the page has no
 * search field. `resultsSelector` is kept as a string because themes re-render their result
 * list and a cached element goes stale; the caller re-queries it on every update.
 */
export function detectAdapter(select, config = {}, adapters = THEME_ADAPTERS) {
  const wanted = config.selector || "auto";
  const wantedResults = config.results_selector || "auto";
  const pickResults = (fallback) => (wantedResults !== "auto" ? wantedResults : fallback);

  const build = (name, input, resultsSelector, dismissSelector) => ({
    name,
    input,
    resultsSelector: resultsSelector || null,
    results: resultsSelector ? select(resultsSelector) : null,
    dismiss: dismissSelector ? select(dismissSelector) : null,
  });

  if (wanted !== "auto") {
    const input = select(wanted);
    return input ? build("custom", input, pickResults(null), null) : null;
  }
  for (const adapter of adapters) {
    const input = select(adapter.input);
    if (!input) continue;
    return build(adapter.name, input, pickResults(adapter.results), adapter.dismiss);
  }
  return null;
}

/**
 * Attach to the theme's search field.
 *
 * `controller` is supplied by ask.js:
 * `{ strings, search(query), open(query), warmup(), resolveUrl(url) }`.
 * Returns a handle with `detach()`, or null when no search field was found.
 */
export function attachSearchBox(manifest, controller) {
  const cfg = manifest.search_box;
  const adapter = detectAdapter((sel) => document.querySelector(sel), cfg);
  if (!adapter) {
    console.warn(
      "[mkdocs-ask] no search field found on this page, so nothing is attached. Enable a " +
        "search plugin, or set search_box.selector to your theme's input.",
    );
    return null;
  }

  const t = controller.strings;
  const block = document.createElement("section");
  block.className = `mkask-sb mkask-sb--${adapter.name}`;
  block.setAttribute("aria-label", t.searchboxHeading);
  block.hidden = true;

  const title = document.createElement("span");
  title.className = "mkask-sb__title";
  title.textContent = cfg.mode === "augment" ? t.searchboxHeading : t.searchboxHandoff;

  const askButton = document.createElement("button");
  askButton.type = "button";
  askButton.className = "mkask-sb__ask";
  askButton.textContent = t.searchboxAsk;

  const head = document.createElement("div");
  head.className = "mkask-sb__head";
  head.append(title, askButton);

  const list = document.createElement("ol");
  list.className = "mkask-sb__list";

  const status = document.createElement("p");
  status.className = "mkask-sb__status";
  status.hidden = true;

  block.append(head, status, list);

  let token = 0;
  let timer = null;
  let lastQuery = "";

  askButton.addEventListener("click", () => {
    dismissThemeSearch();
    controller.open(lastQuery);
  });

  /**
   * Keep the block just above the theme's result list. The list is re-queried every time,
   * because a theme that re-renders its results may have replaced the element we first saw.
   */
  function place() {
    const anchor = adapter.resultsSelector
      ? document.querySelector(adapter.resultsSelector)
      : null;
    if (anchor?.parentNode) {
      if (block.nextSibling !== anchor) anchor.parentNode.insertBefore(block, anchor);
      return;
    }
    if (!block.isConnected) adapter.input.parentNode?.appendChild(block);
  }

  function dismissThemeSearch() {
    // Material drives its search overlay from a checkbox, and listens for change events,
    // so setting the property alone would leave the dropdown open.
    const toggle = adapter.dismiss;
    if (toggle && "checked" in toggle) {
      toggle.checked = false;
      toggle.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function hide() {
    block.hidden = true;
    list.replaceChildren();
    status.hidden = true;
  }

  function renderResults(results) {
    list.replaceChildren();
    for (const r of results.slice(0, cfg.max_results)) {
      const crumbs = r.breadcrumbs.slice(0, -1).join(" > ");
      const link = document.createElement("a");
      link.className = "mkask-sb__link";
      link.href = controller.resolveUrl(r.url);
      if (crumbs) {
        const c = document.createElement("span");
        c.className = "mkask-sb__crumbs";
        c.textContent = crumbs;
        link.append(c);
      }
      const h = document.createElement("strong");
      h.textContent = r.heading;
      link.append(h);

      const snippet = document.createElement("p");
      snippet.className = "mkask-sb__snippet";
      snippet.textContent = r.snippet;

      const item = document.createElement("li");
      item.append(link, snippet);
      list.append(item);
    }
  }

  async function update(query) {
    lastQuery = query;
    if (query.length < cfg.min_chars) return hide();

    place();
    block.hidden = false;
    askButton.textContent = `${t.searchboxAsk}: ${truncate(query, 40)}`;

    if (cfg.mode !== "augment") return;

    const mine = ++token;
    status.hidden = false;
    status.textContent = t.loading;
    try {
      const { results } = await controller.search(query);
      if (mine !== token) return; // a newer keystroke won
      status.hidden = results.length > 0;
      status.textContent = results.length ? "" : t.searchboxEmpty;
      renderResults(results);
    } catch (err) {
      if (mine !== token) return;
      list.replaceChildren();
      status.hidden = false;
      status.textContent = err.message;
    }
  }

  function onInput(event) {
    const query = String(event.target.value || "").trim();
    clearTimeout(timer);
    if (!query) return hide();
    timer = setTimeout(() => update(query), cfg.debounce_ms);
  }

  /**
   * Focusing the field is the earliest honest signal that someone is about to search, so it is
   * where `ui.preload: focus` starts fetching the query encoder. Handoff mode deliberately
   * skips this: its whole point is that nothing downloads until the reader opens the panel.
   */
  function onFocus() {
    if (manifest.ui.preload === "focus" && cfg.mode === "augment") controller.warmup();
  }

  adapter.input.addEventListener("input", onInput);
  adapter.input.addEventListener("search", onInput); // the clear button on input[type=search]
  adapter.input.addEventListener("focus", onFocus, { once: true });

  return {
    adapter: adapter.name,
    detach() {
      clearTimeout(timer);
      adapter.input.removeEventListener("input", onInput);
      adapter.input.removeEventListener("search", onInput);
      adapter.input.removeEventListener("focus", onFocus);
      block.remove();
    },
  };
}

function truncate(text, max) {
  return text.length <= max ? text : `${text.slice(0, max - 1)}\u2026`;
}
