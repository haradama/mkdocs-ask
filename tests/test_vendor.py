from __future__ import annotations

import io

import pytest

from mkdocs_ask import vendor
from mkdocs_ask.models import resolve_model

JSDELIVR = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3"


def test_parse_version_reads_the_bundle_constant():
    assert vendor.parse_version('...M={version:"3.8.1",backends:{onnx:{}}}...') == "3.8.1"
    assert vendor.parse_version("no version here") is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (JSDELIVR, "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1/dist/"),
        (JSDELIVR + "/", "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1/dist/"),
        ("https://cdn.jsdelivr.net/npm/pkg@2", "https://cdn.jsdelivr.net/npm/pkg@2.0.0/dist/"),
    ],
)
def test_dist_base_for_jsdelivr(url, expected):
    version = "3.8.1" if "transformers" in url else "2.0.0"
    assert vendor.dist_base_for(url, version) == expected


@pytest.mark.parametrize(
    "url",
    [
        "/assets/vendor/transformers.min.js",  # already self-hosted
        "https://example.com/transformers.js",
        "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3/dist/transformers.min.js",
    ],
)
def test_dist_base_for_gives_up_on_anything_else(url):
    # Giving up matters: guessing a wasm location would silently keep a CDN request alive.
    assert vendor.dist_base_for(url, "3.8.1") is None


def fake_downloads(monkeypatch, bundle: str = 'x={version:"3.8.1"}'):
    """Stub only the network call, so the real caching in `_download` is still exercised."""
    requested: list[str] = []

    def fake_urlopen(url, *args, **kwargs):
        requested.append(url)
        payload = bundle if url.rstrip("/").endswith("@3") else "binary"
        return io.BytesIO(payload.encode())

    monkeypatch.setattr(vendor.urllib.request, "urlopen", fake_urlopen)
    return requested


def test_vendor_runtime_lays_out_library_and_wasm(tmp_path, monkeypatch):
    requested = fake_downloads(monkeypatch)
    out = tmp_path / "site" / "assets" / "ask"

    result = vendor.vendor_runtime(
        out_dir=out,
        cache_dir=tmp_path / "cache",
        transformers_url=JSDELIVR,
        spec=None,  # keyword-only build: nothing to vendor for the model
        browser_dtype="q8",
    )

    assert result is not None
    assert result.transformers_url == "vendor/transformers/transformers.min.js"
    assert result.wasm_paths == "vendor/transformers/"
    assert result.model_base_url is None
    assert (out / "vendor/transformers/transformers.min.js").exists()
    for name in vendor.ORT_FILES:
        assert (out / "vendor/transformers" / name).exists()
    assert any(u.endswith("ort-wasm-simd-threaded.jsep.wasm") for u in requested)
    assert all(u.startswith("https://cdn.jsdelivr.net/") for u in requested)


def test_vendor_runtime_reuses_the_cache_on_a_second_build(tmp_path, monkeypatch):
    requested = fake_downloads(monkeypatch)
    args = dict(
        cache_dir=tmp_path / "cache", transformers_url=JSDELIVR, spec=None, browser_dtype="q8"
    )
    vendor.vendor_runtime(out_dir=tmp_path / "a", **args)
    first = len(requested)
    vendor.vendor_runtime(out_dir=tmp_path / "b", **args)

    assert first == 3, "the library bundle plus the two ONNX Runtime files"
    assert len(requested) == first, "the second build must not hit the network"
    assert (tmp_path / "b" / "vendor/transformers/transformers.min.js").exists()


def test_vendor_runtime_gives_up_without_a_version(tmp_path, monkeypatch):
    fake_downloads(monkeypatch, bundle="a bundle with no version constant")
    result = vendor.vendor_runtime(
        out_dir=tmp_path / "site",
        cache_dir=tmp_path / "cache",
        transformers_url=JSDELIVR,
        spec=None,
        browser_dtype="q8",
    )
    assert result is None, "falling back to the CDN beats shipping a broken site"


def test_vendor_runtime_gives_up_on_a_non_jsdelivr_library(tmp_path, monkeypatch):
    fake_downloads(monkeypatch)
    result = vendor.vendor_runtime(
        out_dir=tmp_path / "site",
        cache_dir=tmp_path / "cache",
        transformers_url="https://example.com/transformers.min.js",
        spec=None,
        browser_dtype="q8",
    )
    assert result is None


def test_vendor_runtime_copies_the_model_files(tmp_path, monkeypatch):
    fake_downloads(monkeypatch)
    spec = resolve_model("multilingual-e5-small")
    hub = tmp_path / "hub"

    def fake_hf_hub_download(repo_id, filename, **kwargs):
        if filename in vendor.MODEL_FILES_OPTIONAL:
            raise FileNotFoundError(filename)
        path = hub / repo_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        return str(path)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_hf_hub_download)
    out = tmp_path / "site"
    result = vendor.vendor_runtime(
        out_dir=out,
        cache_dir=tmp_path / "cache",
        transformers_url=JSDELIVR,
        spec=spec,
        browser_dtype="q8",
    )

    assert result is not None
    assert result.model_base_url == "vendor/models/"
    base = out / "vendor/models" / spec.onnx_id
    # The layout must match what Transformers.js asks for: <base>/<model-id>/<file>.
    for name in (*vendor.MODEL_FILES_REQUIRED, "onnx/model_quantized.onnx"):
        assert (base / name).exists(), name
    assert not (base / "special_tokens_map.json").exists(), "absent optional files are skipped"


def test_a_failing_model_download_still_vendors_the_library(tmp_path, monkeypatch):
    fake_downloads(monkeypatch)
    import huggingface_hub

    def boom(*a, **k):
        raise ConnectionError("hub unreachable")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", boom)
    result = vendor.vendor_runtime(
        out_dir=tmp_path / "site",
        cache_dir=tmp_path / "cache",
        transformers_url=JSDELIVR,
        spec=resolve_model("multilingual-e5-small"),
        browser_dtype="q8",
    )
    assert result is not None
    assert result.model_base_url is None, "the model falls back to the hub, the library does not"


def test_only_http_and_https_are_vendored(tmp_path, monkeypatch, caplog):
    """A file:// URL would copy an arbitrary local path into the published site.

    The URL comes from mkdocs.yml, so this is about not turning a config typo into a leak
    rather than about a hostile author.
    """
    calls = []
    monkeypatch.setattr(
        vendor.urllib.request, "urlopen", lambda *a, **k: calls.append(a) or io.BytesIO(b"")
    )
    with caplog.at_level("WARNING"):
        result = vendor.vendor_runtime(
            out_dir=tmp_path / "site",
            cache_dir=tmp_path / "cache",
            transformers_url="file:///etc/passwd",
            spec=None,
            browser_dtype="q8",
        )
    assert result is None
    assert calls == [], "nothing was opened"
    assert "refusing to vendor from a file URL" in caplog.text


def test_downloads_carry_a_timeout(tmp_path, monkeypatch):
    # A CDN that accepts the connection and then stalls must not hang the build.
    seen = {}

    def fake_urlopen(url, *args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return io.BytesIO(b'x={version:"3.8.1"}')

    monkeypatch.setattr(vendor.urllib.request, "urlopen", fake_urlopen)
    vendor.vendor_runtime(
        out_dir=tmp_path / "site",
        cache_dir=tmp_path / "cache",
        transformers_url=JSDELIVR,
        spec=None,
        browser_dtype="q8",
    )
    assert seen["timeout"] == vendor.DOWNLOAD_TIMEOUT_SECONDS
    assert seen["timeout"] > 0
