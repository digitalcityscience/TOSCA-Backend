"""
Tests for the legacy HTML → Editor.js block converter.

These cover every mapping documented in :mod:`tosca_api.apps.core.legacy_html`
plus the edge cases called out in Task 7.4 acceptance criteria:

- blank input
- plain text
- rich HTML with headers / paragraphs / lists / nested lists / blockquote
  / pre-code / inline code / ``<br>``
- orphan top-level text folded into paragraphs
- unknown non-media tags don't synthesise block types
- rows containing ``<img>`` / ``<figure>`` / ``<figcaption>`` raise
  :class:`LegacyHtmlMediaError`
- deterministic output (idempotent across runs)
"""

from __future__ import annotations

import pytest

from tosca_api.apps.core.legacy_html import (
    LegacyHtmlMediaError,
    convert_legacy_html,
)


# ----------------------------------------------------------------------
# Blank / plain text
# ----------------------------------------------------------------------


def test_blank_input_returns_empty_blocks():
    assert convert_legacy_html("") == {"blocks": []}
    assert convert_legacy_html("   \n") == {"blocks": []}


def test_none_input_returns_empty_blocks():
    assert convert_legacy_html(None) == {"blocks": []}


def test_plain_text_becomes_single_paragraph():
    out = convert_legacy_html("Hello world")
    assert out == {
        "blocks": [{"type": "paragraph", "data": {"text": "Hello world"}}]
    }


# ----------------------------------------------------------------------
# Headers / paragraphs
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tag,level", [("h1", 1), ("h2", 2), ("h3", 3), ("h4", 4)])
def test_header_tags_map_to_header_blocks(tag, level):
    out = convert_legacy_html(f"<{tag}>Title</{tag}>")
    assert out == {
        "blocks": [{"type": "header", "data": {"text": "Title", "level": level}}]
    }


def test_paragraph_preserves_inline_markup():
    html = '<p>Hello <strong>bold</strong> <em>it</em> <a href="https://ex.com">link</a></p>'
    out = convert_legacy_html(html)
    assert out["blocks"][0]["type"] == "paragraph"
    text = out["blocks"][0]["data"]["text"]
    assert "<strong>bold</strong>" in text
    assert "<em>it</em>" in text
    assert 'href="https://ex.com"' in text


def test_paragraph_preserves_br():
    out = convert_legacy_html("<p>line1<br>line2</p>")
    assert out["blocks"][0]["data"]["text"] == "line1<br>line2"


def test_b_and_i_normalized_to_strong_and_em():
    out = convert_legacy_html("<p><b>x</b> <i>y</i></p>")
    text = out["blocks"][0]["data"]["text"]
    assert "<strong>x</strong>" in text
    assert "<em>y</em>" in text


def test_inline_code_inside_paragraph_stays_inline():
    out = convert_legacy_html("<p>run <code>ls -la</code> now</p>")
    assert out["blocks"][0]["type"] == "paragraph"
    assert "<code>ls -la</code>" in out["blocks"][0]["data"]["text"]


# ----------------------------------------------------------------------
# Lists
# ----------------------------------------------------------------------


def test_flat_unordered_list():
    out = convert_legacy_html("<ul><li>a</li><li>b</li></ul>")
    assert out == {
        "blocks": [
            {
                "type": "list",
                "data": {
                    "style": "unordered",
                    "items": [
                        {"content": "a", "items": []},
                        {"content": "b", "items": []},
                    ],
                    "meta": {},
                },
            }
        ]
    }


def test_flat_ordered_list():
    out = convert_legacy_html("<ol><li>one</li><li>two</li></ol>")
    assert out["blocks"][0]["data"]["style"] == "ordered"
    assert [i["content"] for i in out["blocks"][0]["data"]["items"]] == ["one", "two"]


def test_nested_list_preserves_shape():
    html = (
        "<ul>"
        "<li>outer1<ul><li>inner1</li><li>inner2</li></ul></li>"
        "<li>outer2</li>"
        "</ul>"
    )
    out = convert_legacy_html(html)
    items = out["blocks"][0]["data"]["items"]
    assert items[0]["content"] == "outer1"
    assert [c["content"] for c in items[0]["items"]] == ["inner1", "inner2"]
    assert items[1]["content"] == "outer2"
    assert items[1]["items"] == []


# ----------------------------------------------------------------------
# Blockquote / pre / code
# ----------------------------------------------------------------------


def test_blockquote_becomes_quote_block():
    out = convert_legacy_html("<blockquote>wisdom</blockquote>")
    assert out == {
        "blocks": [
            {"type": "quote", "data": {"text": "wisdom", "caption": ""}}
        ]
    }


def test_pre_code_becomes_code_block():
    out = convert_legacy_html("<pre><code>print(1)\nprint(2)</code></pre>")
    assert out == {
        "blocks": [{"type": "code", "data": {"code": "print(1)\nprint(2)"}}]
    }


def test_pre_without_inner_code_becomes_code_block():
    out = convert_legacy_html("<pre>raw text</pre>")
    assert out == {"blocks": [{"type": "code", "data": {"code": "raw text"}}]}


def test_pre_strips_single_leading_trailing_newline():
    out = convert_legacy_html("<pre>\nhello\n</pre>")
    assert out["blocks"][0]["data"]["code"] == "hello"


# ----------------------------------------------------------------------
# Orphan text + unknown tags
# ----------------------------------------------------------------------


def test_orphan_text_between_blocks_becomes_paragraph():
    out = convert_legacy_html("hi there<p>inside</p>trailing")
    types = [b["type"] for b in out["blocks"]]
    assert types == ["paragraph", "paragraph", "paragraph"]
    texts = [b["data"]["text"] for b in out["blocks"]]
    assert texts[0].strip() == "hi there"
    assert texts[1] == "inside"
    assert texts[2].strip() == "trailing"


def test_unknown_non_media_tag_does_not_create_block():
    # <section> is an unknown non-media wrapper; it must not yield a
    # block of type "section". Its <p> child still becomes a paragraph.
    out = convert_legacy_html("<section><p>hello</p></section>")
    assert [b["type"] for b in out["blocks"]] == ["paragraph"]
    assert out["blocks"][0]["data"]["text"] == "hello"


# ----------------------------------------------------------------------
# Media rejection
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "html,expected",
    [
        ("<p>hi</p><img src='x'>", ["img"]),
        ("<figure><img src='x'></figure>", ["figure", "img"]),
        ("<figure><figcaption>caption</figcaption></figure>", ["figcaption", "figure"]),
    ],
)
def test_media_bearing_html_aborts(html, expected):
    with pytest.raises(LegacyHtmlMediaError) as excinfo:
        convert_legacy_html(html)
    assert excinfo.value.tags == sorted(set(expected))


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------


def test_conversion_is_idempotent_across_runs():
    html = (
        "<h2>Title</h2>"
        "<p>Intro <strong>bold</strong> text.</p>"
        "<ul><li>a<ul><li>aa</li></ul></li><li>b</li></ul>"
        "<blockquote>q</blockquote>"
        "<pre><code>x=1</code></pre>"
    )
    first = convert_legacy_html(html)
    second = convert_legacy_html(html)
    assert first == second


def test_mixed_document_preserves_expected_block_sequence():
    html = (
        "<h3>Heading</h3>"
        "<p>Para <code>inline</code>.</p>"
        "<ol><li>one</li><li>two</li></ol>"
        "<blockquote>qt</blockquote>"
        "<pre><code>code here</code></pre>"
    )
    out = convert_legacy_html(html)
    assert [b["type"] for b in out["blocks"]] == [
        "header",
        "paragraph",
        "list",
        "quote",
        "code",
    ]
