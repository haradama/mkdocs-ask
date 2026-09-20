import assert from "node:assert/strict";
import { test } from "node:test";
import {
  installNoReferrerFetch,
  isCrossOrigin,
  resolveRuntimeUrl,
} from "../../src/mkdocs_ask/assets/urls.js";

const BASE = "https://docs.internal.example.com/assets/ask/";

test("absolute URLs are left alone", () => {
  const cdn = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3";
  assert.equal(resolveRuntimeUrl(cdn, BASE), cdn);
  assert.equal(resolveRuntimeUrl("//cdn.example.com/x.js", BASE), "//cdn.example.com/x.js");
});

test("vendored paths resolve against the asset directory, not the worker script", () => {
  // A worker would otherwise resolve these against its own URL, which happens to be the same
  // directory today but is not what the path means.
  assert.equal(
    resolveRuntimeUrl("vendor/transformers/transformers.min.js", BASE),
    "https://docs.internal.example.com/assets/ask/vendor/transformers/transformers.min.js",
  );
  assert.equal(
    resolveRuntimeUrl("vendor/models/", BASE),
    "https://docs.internal.example.com/assets/ask/vendor/models/",
  );
});

test("a site served from a sub-path keeps its prefix", () => {
  const base = "https://intranet.example.com/team/docs/assets/ask/";
  assert.equal(
    resolveRuntimeUrl("vendor/transformers/", base),
    "https://intranet.example.com/team/docs/assets/ask/vendor/transformers/",
  );
});

test("empty values pass through untouched", () => {
  assert.equal(resolveRuntimeUrl(null, BASE), null);
  assert.equal(resolveRuntimeUrl("", BASE), "");
});

test("cross-origin detection drives whether the Referer is stripped", () => {
  const here = "https://docs.internal.example.com/assets/ask/worker.js";
  assert.equal(isCrossOrigin("https://huggingface.co/model.onnx", here), true);
  assert.equal(isCrossOrigin("https://cdn.jsdelivr.net/npm/x", here), true);
  assert.equal(isCrossOrigin("https://docs.internal.example.com/assets/ask/chunks.json", here), false);
  assert.equal(isCrossOrigin("vendor/models/config.json", here), false, "vendored is same-origin");
  assert.equal(isCrossOrigin(undefined, here), false, "never throw on a odd input");
});

test("a different port or scheme counts as cross-origin", () => {
  const here = "https://docs.example.com/a/";
  assert.equal(isCrossOrigin("https://docs.example.com:8443/x", here), true);
  assert.equal(isCrossOrigin("http://docs.example.com/x", here), true);
});

test("installNoReferrerFetch strips the Referer from cross-origin requests only", async () => {
  const seen = [];
  const scope = {
    location: { href: "https://docs.internal.example.com/assets/ask/worker.js" },
    fetch: (input, init) => {
      seen.push({ input, init });
      return Promise.resolve("ok");
    },
  };

  assert.equal(installNoReferrerFetch(scope), true);
  await scope.fetch("https://huggingface.co/Xenova/m/onnx/model.onnx");
  await scope.fetch("https://docs.internal.example.com/assets/ask/chunks.json");
  await scope.fetch("vendor/models/Xenova/m/config.json");

  assert.equal(seen[0].init.referrerPolicy, "no-referrer", "the model host learns nothing");
  assert.equal(seen[1].init, undefined, "same-origin requests are untouched");
  assert.equal(seen[2].init, undefined, "so are vendored, relative ones");
});

test("installNoReferrerFetch preserves the caller's own fetch options", async () => {
  const seen = [];
  const scope = {
    location: { href: "https://docs.example.com/a/worker.js" },
    fetch: (input, init) => {
      seen.push(init);
      return Promise.resolve("ok");
    },
  };
  installNoReferrerFetch(scope);
  await scope.fetch("https://cdn.jsdelivr.net/x", { cache: "force-cache", headers: { a: "b" } });

  assert.deepEqual(seen[0], {
    cache: "force-cache",
    headers: { a: "b" },
    referrerPolicy: "no-referrer",
  });
});

test("installNoReferrerFetch is idempotent and tolerates a scope without fetch", () => {
  const scope = { location: { href: "https://x.test/" }, fetch: () => {} };
  assert.equal(installNoReferrerFetch(scope), true);
  assert.equal(installNoReferrerFetch(scope), false, "wrapping twice would nest the wrappers");
  assert.equal(installNoReferrerFetch({}), false);
  assert.equal(installNoReferrerFetch(null), false);
});

test("a Request object is passed through with the policy applied", async () => {
  const seen = [];
  const scope = {
    location: { href: "https://docs.example.com/a/" },
    fetch: (input, init) => {
      seen.push({ input, init });
      return Promise.resolve("ok");
    },
  };
  installNoReferrerFetch(scope);
  const request = { url: "https://huggingface.co/model.onnx" };
  await scope.fetch(request);
  assert.equal(seen[0].input, request);
  assert.equal(seen[0].init.referrerPolicy, "no-referrer");
});

test("an unparseable origin is treated as same-origin rather than throwing", () => {
  // Being wrong in the safe direction here only means a Referer is sent; throwing would take
  // the whole search down.
  assert.equal(isCrossOrigin("https://huggingface.co/x", "not a url"), false);
  assert.equal(isCrossOrigin("https://huggingface.co/x", undefined), false);
});
