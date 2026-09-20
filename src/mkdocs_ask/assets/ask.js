/**
 * mkdocs-ask: UI entry point, loaded as `<script type="module">` on every page.
 *
 * The plugin adds no launcher of its own. It attaches to the theme's search field
 * (searchbox.js), which is the only way in, and owns a chat-style dialog that the field opens
 * on demand. Queries are proxied to worker.js. Nothing heavy loads until the reader touches
 * the search field, and `ui.preload` decides even that.
 */

const ASSET_BASE = new URL("./", import.meta.url);

/**
 * Default UI strings. Every key can be overridden per site with `ui.strings` in mkdocs.yml,
 * which is how you localise the panel (the plugin ships English only and stays language-neutral).
 */
const DEFAULT_STRINGS = {
  found: "Here is what I found:",
  none: "No matching sections found. Try different words.",
  loading: "Searching\u2026",
  ai: "\u2728 Answer with AI",
  aiWorking: "Generating\u2026",
  sources: "Sources",
  close: "Close",
  send: "Send",
  keyword: "keyword",
  hybrid: "hybrid",
  semantic: "semantic",
  searchboxHeading: "Semantic matches",
  searchboxHandoff: "Not finding it?",
  searchboxAsk: "Ask the docs",
  searchboxEmpty: "No semantic matches for this wording.",
};

class AskUI {
  constructor(manifest) {
    this.manifest = manifest;
    this.ui = manifest.ui;
    this.t = { ...DEFAULT_STRINGS, ...(manifest.ui.strings || {}) };
    this.siteBase = new URL("../".repeat(manifest.output_dir.split("/").length), ASSET_BASE);
    this.worker = null;
    this.pending = new Map(); // id -> { resolve, reject, onToken }
    this.nextId = 1;
    this.readyPromise = null;
    this.ready = false;
  }

  // -- DOM ---------------------------------------------------------------------------------

  mount() {
    this.dialog = el("dialog", { class: "mkask-dialog", "aria-label": this.ui.title });
    this.messages = el("div", { class: "mkask-messages" });
    this.status = el("div", { class: "mkask-status", "aria-live": "polite" });
    this.input = el("input", {
      class: "mkask-input",
      type: "text",
      placeholder: this.ui.placeholder,
      autocomplete: "off",
    });
    const form = el("form", { class: "mkask-form" }, [
      this.input,
      el("button", { class: "mkask-send", type: "submit", "aria-label": this.t.send }, "\u27A4"),
    ]);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const q = this.input.value.trim();
      if (q) {
        this.input.value = "";
        this.ask(q);
      }
    });
    const closeBtn = el(
      "button",
      { class: "mkask-close", type: "button", "aria-label": this.t.close },
      "\u2715",
    );
    closeBtn.addEventListener("click", () => this.dialog.close());
    this.dialog.append(
      el("header", { class: "mkask-header" }, [el("h2", {}, this.ui.title), closeBtn]),
      this.messages,
      this.status,
      form,
    );
    this.dialog.addEventListener("click", (e) => {
      if (e.target === this.dialog) this.dialog.close();
    });
    document.body.append(this.dialog);

    this.attachSearchBox();
    if (this.ui.preload === "idle") {
      const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 2000));
      idle(() => this.warmup());
    }
  }

  /** Start the worker and, unless told to wait, begin fetching the query encoder. */
  warmup() {
    return this.ensureWorker()
      .then(() => {
        if (this.ui.preload !== "query") this.worker.postMessage({ type: "warmup" });
      })
      .catch(() => {}); // already reported through the status line
  }

  /** Open the panel, optionally running a query handed over from the theme's search field. */
  open(prefill = "") {
    this.ensureWorker()
      .then(() => this.worker.postMessage({ type: "warmup" }))
      .catch(() => {});
    if (!this.dialog.open) this.dialog.showModal();
    this.input.focus();
    const query = String(prefill).trim();
    if (query && query !== this.lastAsked) {
      this.input.value = "";
      this.ask(query);
    }
  }

  /**
   * Hand the theme's search field everything it needs, without letting it reach into the UI.
   * Kept in its own module so the DOM-facing part can be tested against real theme markup.
   */
  attachSearchBox() {
    return import("./searchbox.js")
      .then(({ attachSearchBox }) =>
        attachSearchBox(this.manifest, {
          strings: this.t,
          search: (query) => this.ensureWorker().then(() => this.request("search", { query })),
          open: (query) => this.open(query),
          warmup: () => this.warmup(),
          resolveUrl: (url) => new URL(url, this.siteBase).href,
        }),
      )
      .catch((err) => console.warn("[mkdocs-ask] search field integration failed:", err));
  }

  // -- worker ------------------------------------------------------------------------------

  /**
   * Start the worker once and resolve when its index is loaded. A failure before that point,
   * such as a missing manifest, rejects the promise so callers fail fast instead of waiting
   * forever on a search that can never answer.
   */
  ensureWorker() {
    if (this.readyPromise) return this.readyPromise;
    this.worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
    this.readyPromise = new Promise((resolve, reject) => {
      this.settleReady = { resolve, reject };
      this.worker.onmessage = (e) => this.onMessage(e.data);
      this.worker.onerror = (e) => {
        const message = `Worker error: ${e.message || "failed to start"}`;
        this.setStatus(message);
        reject(new Error(message));
      };
    });
    this.worker.postMessage({ type: "init", baseUrl: ASSET_BASE.href });
    return this.readyPromise;
  }

  onMessage(msg) {
    switch (msg.type) {
      case "ready":
        this.ready = true;
        this.settleReady.resolve();
        break;
      case "status":
        this.setStatus(msg.text, msg.progress);
        break;
      case "results":
        this.pending.get(msg.id)?.resolve(msg);
        this.pending.delete(msg.id);
        break;
      case "token":
        this.pending.get(msg.id)?.onToken(msg.text);
        break;
      case "answer_done":
        this.pending.get(msg.id)?.resolve(msg);
        this.pending.delete(msg.id);
        break;
      case "error":
        if (msg.id && this.pending.has(msg.id)) {
          this.pending.get(msg.id).reject(new Error(msg.message));
          this.pending.delete(msg.id);
          break;
        }
        this.setStatus(msg.message);
        if (!this.ready) this.settleReady.reject(new Error(msg.message));
        break;
    }
  }

  request(type, payload, onToken) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, onToken: onToken || (() => {}) });
      this.worker.postMessage({ type, id, ...payload });
    });
  }

  setStatus(text, progress) {
    this.status.textContent = text || "";
    this.status.style.setProperty("--mkask-progress", progress == null ? "0" : String(progress));
    this.status.classList.toggle("mkask-status--busy", progress != null && progress < 1);
  }

  // -- conversation ------------------------------------------------------------------------

  async ask(query) {
    this.lastAsked = query;
    this.addMessage("user", query);
    const bubble = this.addMessage("assistant", this.t.loading);
    try {
      await this.ensureWorker();
      const { results, mode } = await this.request("search", { query });
      this.renderResults(bubble, query, results, mode);
    } catch (err) {
      bubble.textContent = `\u26A0 ${err.message}`;
    }
  }

  addMessage(role, text) {
    const node = el("div", { class: `mkask-msg mkask-msg--${role}` }, text);
    this.messages.append(node);
    this.messages.scrollTop = this.messages.scrollHeight;
    return node;
  }

  renderResults(bubble, query, results, mode) {
    bubble.replaceChildren();
    if (!results.length) {
      bubble.textContent = this.t.none;
      return;
    }
    const list = el("ol", { class: "mkask-results" });
    for (const r of results) {
      const crumbs = r.breadcrumbs.slice(0, -1).join(" \u203A ");
      list.append(
        el("li", {}, [
          el("a", { href: new URL(r.url, this.siteBase).href, class: "mkask-result" }, [
            crumbs ? el("span", { class: "mkask-crumbs" }, crumbs) : null,
            el("strong", {}, r.heading),
          ]),
          el("p", { class: "mkask-snippet" }, r.snippet),
        ]),
      );
    }
    bubble.append(
      el("p", { class: "mkask-intro" }, [this.t.found, " ", el("span", { class: "mkask-mode" }, this.t[mode] || mode)]),
      list,
    );
    if (this.manifest.ai_answer?.enabled) {
      const btn = el("button", { class: "mkask-ai", type: "button" }, this.t.ai);
      btn.addEventListener("click", () => this.answer(query, results, btn));
      bubble.append(btn);
    }
    this.messages.scrollTop = this.messages.scrollHeight;
  }

  async answer(query, results, button) {
    button.disabled = true;
    button.textContent = this.t.aiWorking;
    const bubble = this.addMessage("assistant", "");
    const body = el("div", { class: "mkask-answer" });
    bubble.append(body);
    let text = "";
    try {
      await this.request("answer", { query, ids: results.map((r) => r.id) }, (token) => {
        text += token;
        body.textContent = text;
        this.messages.scrollTop = this.messages.scrollHeight;
      });
      body.replaceChildren(...linkCitations(text, results, this.siteBase));
      bubble.append(
        el("p", { class: "mkask-sources" }, [
          `${this.t.sources}: `,
          ...results.flatMap((r, i) => [
            el("a", { href: new URL(r.url, this.siteBase).href }, `[${i + 1}] ${r.heading}`),
            " ",
          ]),
        ]),
      );
    } catch (err) {
      body.textContent = `\u26A0 ${err.message}`;
    } finally {
      button.remove();
    }
  }
}

/** Replace [n] markers with links to the n-th result. */
function linkCitations(text, results, siteBase) {
  const nodes = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const n = Number(m[1]);
    nodes.push(text.slice(last, m.index));
    const r = results[n - 1];
    nodes.push(r ? el("a", { href: new URL(r.url, siteBase).href, class: "mkask-cite" }, m[0]) : m[0]);
    last = m.index + m[0].length;
  }
  nodes.push(text.slice(last));
  return nodes;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) node.setAttribute(k, v);
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child == null) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

async function main() {
  if (window.__mkdocsAsk) return; // e.g. re-executed by instant navigation
  window.__mkdocsAsk = true;
  try {
    const res = await fetch(new URL("manifest.json", ASSET_BASE));
    if (!res.ok) throw new Error(`manifest.json: HTTP ${res.status}`);
    const manifest = await res.json();
    new AskUI(manifest).mount();
  } catch (err) {
    console.warn("[mkdocs-ask] disabled:", err);
  }
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", main);
else main();
