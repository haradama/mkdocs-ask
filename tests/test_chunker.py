from __future__ import annotations

from mkdocs_ask.chunker import chunk_page, split_text
from mkdocs_ask.config import ChunkingConfig


def cfg(**overrides) -> ChunkingConfig:
    c = ChunkingConfig()
    c.load_dict(overrides)
    errors, warnings = c.validate()
    assert not errors, errors
    return c


HTML = """
<h1 id="getting-started">Getting started
<a class="headerlink" href="#getting-started">\u00b6</a></h1>
<p>Intro paragraph about the project.</p>
<h2 id="install">Install</h2>
<p>Run <code>pip install foo</code> to install it.</p>
<pre><code>pip install foo
foo --version</code></pre>
<h3 id="from-source">From source</h3>
<p>Clone the repository and run the build.</p>
<h2 id="usage">Usage</h2>
<script>ignored()</script>
<p>Call <code>getUserById</code>.</p>
<table><tr><th>Key</th><th>Value</th></tr><tr><td>a</td><td>1</td></tr></table>
"""


def test_sections_get_anchor_urls_and_breadcrumbs():
    chunks = chunk_page(HTML, page_url="guide/", page_title="Getting started", cfg=cfg())
    by_url = {c.url: c for c in chunks}
    assert "guide/#install" in by_url
    assert "guide/#from-source" in by_url
    assert "guide/#usage" in by_url
    assert by_url["guide/#from-source"].breadcrumbs == ["Getting started", "Install", "From source"]
    # h1 equal to the page title is not duplicated in the breadcrumbs
    assert by_url["guide/#install"].breadcrumbs == ["Getting started", "Install"]
    assert by_url["guide/#install"].heading == "Install"


def test_headerlink_and_script_are_ignored_code_is_kept():
    chunks = chunk_page(HTML, page_url="guide/", page_title="Getting started", cfg=cfg())
    text = "\n".join(c.text for c in chunks)
    assert "\u00b6" not in text
    assert "ignored()" not in text
    assert "foo --version" in text
    assert "Key Value" in text  # table cells are space-separated
    assert all("\u00b6" not in c.heading for c in chunks)


def test_include_code_false_drops_pre_blocks():
    chunks = chunk_page(
        HTML, page_url="guide/", page_title="Getting started", cfg=cfg(include_code=False)
    )
    text = "\n".join(c.text for c in chunks)
    assert "foo --version" not in text
    assert "pip install foo" in text  # inline <code> outside <pre> is still kept


def test_preamble_before_first_heading_uses_page_title():
    html = "<p>Only a preamble, no headings at all in this page body.</p>"
    chunks = chunk_page(html, page_url="x/", page_title="X", cfg=cfg())
    assert len(chunks) == 1
    assert chunks[0].url == "x/"
    assert chunks[0].heading == "X"
    assert chunks[0].breadcrumbs == ["X"]


def test_split_text_respects_max_chars_and_overlaps():
    sentences = [f"Sentence number {i} is here and it is reasonably long." for i in range(40)]
    text = " ".join(sentences)
    pieces = split_text(text, max_chars=300, overlap=60, min_chars=20)
    assert len(pieces) > 1
    assert all(len(p) <= 300 for p in pieces)
    # overlap: the tail of chunk N reappears at the head of chunk N+1
    for prev, nxt in zip(pieces, pieces[1:], strict=False):
        tail = prev[-30:].split()[-1]
        assert tail in nxt[:120]
    assert "Sentence number 39" in pieces[-1]


def test_split_text_splits_on_cjk_sentence_terminator():
    # U+3002 is the ideographic full stop. CJK literals are escaped so the
    # repository stays ASCII-only; the behaviour under test is CJK sentence splitting.
    text = (
        "\u8a8d\u8a3c\u65b9\u5f0f\u306b\u306fOAuth 2.0\u3068API"
        "\u30ad\u30fc\u304c\u3042\u308a\u307e\u3059\u3002" * 40
    )
    pieces = split_text(text, max_chars=200, overlap=0, min_chars=10)
    assert len(pieces) > 1
    assert all(len(p) <= 200 for p in pieces)
    assert all(p.endswith("\u3002") for p in pieces)


def test_split_text_drops_tiny_and_hard_splits_giant_tokens():
    assert split_text("tiny", max_chars=100, overlap=0, min_chars=10) == []
    giant = "x" * 1000
    pieces = split_text(giant, max_chars=100, overlap=10, min_chars=1)
    assert all(len(p) <= 100 for p in pieces)
    assert sum(len(p) for p in pieces) >= 1000


def test_embedding_text_has_prefix_and_breadcrumbs():
    chunks = chunk_page(HTML, page_url="guide/", page_title="Getting started", cfg=cfg())
    c = next(c for c in chunks if c.url == "guide/#from-source")
    assert c.embedding_text("passage: ").startswith(
        "passage: Getting started > Install > From source\n"
    )
    assert c.embedding_text("passage: ", include_breadcrumbs=False).startswith("passage: Clone")
