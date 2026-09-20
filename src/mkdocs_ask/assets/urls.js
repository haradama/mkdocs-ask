/**
 * mkdocs-ask: resolving the runtime's dependency URLs.
 *
 * `runtime.*` entries are either absolute URLs pointing at a CDN, or, once the dependencies
 * have been vendored into the site, paths relative to the plugin's asset directory. Workers
 * resolve bare relative paths against their own script URL, which is not what those paths
 * mean, so every one of them goes through here.
 */

const ABSOLUTE = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i;

/** Resolve one runtime URL against the asset directory. Absolute URLs pass through. */
export function resolveRuntimeUrl(url, baseUrl) {
  if (!url) return url;
  return ABSOLUTE.test(url) ? url : new URL(url, baseUrl).href;
}

/** True when `url` would leave the origin the page was served from. */
export function isCrossOrigin(url, origin) {
  try {
    return new URL(url, origin).origin !== new URL(origin).origin;
  } catch {
    return false;
  }
}

/**
 * Wrap a scope's `fetch` so cross-origin requests carry no `Referer`.
 *
 * Transformers.js and ONNX Runtime fetch the model weights and the wasm binaries themselves,
 * so the worker's own `fetch` is the only place to intervene. Without this, a CDN and the
 * model host learn the origin of the documentation site from every reader who searches.
 * Same-origin requests are left untouched. The dynamic `import()` of the library is beyond
 * reach, since module requests take no options; only vendoring removes that last one.
 *
 * Returns true when it patched the scope, false when it was already patched or cannot.
 */
export function installNoReferrerFetch(scope) {
  if (!scope || typeof scope.fetch !== "function" || scope.__mkaskNoReferrer) return false;
  scope.__mkaskNoReferrer = true;
  const original = scope.fetch.bind(scope);
  const here = scope.location?.href;
  scope.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input?.url;
    if (!isCrossOrigin(url, here)) return original(input, init);
    return original(input, { ...(init || {}), referrerPolicy: "no-referrer" });
  };
  return true;
}
