"""Copy the browser runtime's third-party dependencies into the site at build time.

Without this, a reader's browser fetches the Transformers.js bundle and the ONNX Runtime
WebAssembly binaries from jsDelivr, and the model weights from huggingface.co. Those requests
carry no document text and no query, but they do carry the reader's IP address and a `Referer`
naming the documentation site. For docs that are internal, the existence and hostname of the
site is itself information, and there is no way to strip the `Referer` from a dynamic
`import()`.

Vendoring removes the question instead of mitigating it: every request becomes same-origin, so
there is no third party to leak to, and the published site works with no internet access at
all. The cost is about 160 MB added to `site/`, most of it the model.
"""

from __future__ import annotations

import re
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from mkdocs.plugins import get_plugin_logger

from .models import ONNX_FILES, EmbeddingModelSpec

log = get_plugin_logger("ask")

VENDOR_DIR = "vendor"
LIB_DIR = f"{VENDOR_DIR}/transformers"
MODELS_DIR = f"{VENDOR_DIR}/models"

# The ONNX Runtime artifacts Transformers.js loads. The jsep build serves both the
# WebAssembly and the WebGPU execution providers, so it is needed on either path.
ORT_FILES = ("ort-wasm-simd-threaded.jsep.wasm", "ort-wasm-simd-threaded.jsep.mjs")

# Files Transformers.js needs to build a feature-extraction pipeline.
MODEL_FILES_REQUIRED = ("config.json", "tokenizer.json", "tokenizer_config.json")
# Requested but tolerated missing. Vendored when the repository has them, to keep the
# browser console free of 404s.
MODEL_FILES_OPTIONAL = ("special_tokens_map.json",)

# A jsDelivr npm URL, with an optional @scope/ before the package name, ending at the
# version. Anything with a path after the version is a direct file reference, which tells
# us nothing about where the sibling dist/ directory is.
_JSDELIVR_NPM = re.compile(r"^(https://cdn\.jsdelivr\.net/npm/(?:@[^/@]+/)?[^/@]+)@[^/]+/?$")
_VERSION = re.compile(r'version\s*:\s*"(\d+\.\d+\.\d+)"')

# A CDN that does not answer within this window is treated like one that is unreachable:
# the build falls back to the CDN URLs rather than hanging.
DOWNLOAD_TIMEOUT_SECONDS = 60


@dataclass
class VendoredRuntime:
    """Site-relative paths, ready to be written into the manifest's `runtime` section."""

    transformers_url: str
    wasm_paths: str
    model_base_url: str | None
    bytes_written: int


def parse_version(bundle: str) -> str | None:
    """Read the package version out of the Transformers.js bundle."""
    match = _VERSION.search(bundle)
    return match.group(1) if match else None


def dist_base_for(transformers_url: str, version: str) -> str | None:
    """Work out where the ONNX Runtime binaries live, given the library URL.

    Only jsDelivr npm URLs can be mapped automatically. Anything else means the site is
    already self-hosting the library, in which case it must point `runtime.wasm_paths` at its
    own copy of the binaries.
    """
    match = _JSDELIVR_NPM.match(transformers_url.rstrip("/"))
    return f"{match.group(1)}@{version}/dist/" if match else None


def _download(url: str, dest: Path) -> int:
    """Fetch `url` to `dest` unless it is already there. Returns the file size.

    Callers count bytes from `_copy` instead, so that the reported total is what the site
    actually grows by rather than double-counting the cached download.
    """
    if dest.exists() and dest.stat().st_size > 0:
        return dest.stat().st_size
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme not in ("http", "https"):
        # The URL comes from mkdocs.yml. Anything but http(s), such as file://, would copy an
        # arbitrary local path into the published site.
        raise OSError(f"refusing to vendor from a {scheme or 'schemeless'} URL: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info(f"vendoring {url}")
    with (
        urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response,
        open(tmp, "wb") as fh,
    ):
        shutil.copyfileobj(response, fh)
    tmp.replace(dest)
    return dest.stat().st_size


def _copy(src: Path, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest.stat().st_size


def vendor_runtime(
    *,
    out_dir: Path,
    cache_dir: Path,
    transformers_url: str,
    spec: EmbeddingModelSpec | None,
    browser_dtype: str,
) -> VendoredRuntime | None:
    """Place the library, the ONNX Runtime binaries and the model under `out_dir`.

    Downloads are cached under `cache_dir`, so only the first build needs the network.
    Returns None when vendoring could not be completed, leaving the CDN defaults in place.
    """
    lib_cache = cache_dir / VENDOR_DIR
    total = 0

    try:
        bundle_path = lib_cache / "transformers.min.js"
        _download(transformers_url, bundle_path)
        version = parse_version(bundle_path.read_text(encoding="utf-8", errors="ignore"))
        if not version:
            log.warning("could not read the Transformers.js version; skipping vendoring")
            return None

        dist_base = dist_base_for(transformers_url, version)
        if dist_base is None:
            log.warning(
                f"runtime.transformers_url ({transformers_url}) is not a jsDelivr npm URL, so "
                "the ONNX Runtime binaries cannot be located automatically. Set "
                "runtime.wasm_paths to your own copy and leave runtime.vendor off."
            )
            return None

        total += _copy(bundle_path, out_dir / LIB_DIR / "transformers.min.js")
        for name in ORT_FILES:
            cached = lib_cache / version / name
            _download(f"{dist_base}{name}", cached)
            total += _copy(cached, out_dir / LIB_DIR / name)
    except OSError as exc:
        log.warning(f"could not vendor the browser runtime ({exc}); falling back to the CDN")
        return None

    model_base: str | None = None
    if spec is not None:
        try:
            total += _vendor_model(out_dir, cache_dir, spec, browser_dtype)
            model_base = f"{MODELS_DIR}/"
        except Exception as exc:  # any hub failure is non-fatal
            log.warning(f"could not vendor the embedding model ({exc}); it stays on the hub")

    log.info(f"vendored the browser runtime into {out_dir / VENDOR_DIR} ({total / 1e6:.0f} MB)")
    return VendoredRuntime(
        transformers_url=f"{LIB_DIR}/transformers.min.js",
        wasm_paths=f"{LIB_DIR}/",
        model_base_url=model_base,
        bytes_written=total,
    )


def _vendor_model(
    out_dir: Path, cache_dir: Path, spec: EmbeddingModelSpec, browser_dtype: str
) -> int:
    """Copy the model files Transformers.js requests into `<out_dir>/vendor/models/<id>/`."""
    from huggingface_hub import hf_hub_download

    target = out_dir / MODELS_DIR / spec.onnx_id
    wanted = [*MODEL_FILES_REQUIRED, ONNX_FILES[browser_dtype]]
    total = 0
    for filename in wanted:
        path = hf_hub_download(
            spec.onnx_id, filename, cache_dir=str(cache_dir / "models"), revision="main"
        )
        total += _copy(Path(path), target / filename)
    for filename in MODEL_FILES_OPTIONAL:
        try:
            path = hf_hub_download(
                spec.onnx_id, filename, cache_dir=str(cache_dir / "models"), revision="main"
            )
        except Exception:  # optional files are allowed to be absent
            continue
        total += _copy(Path(path), target / filename)
    return total
