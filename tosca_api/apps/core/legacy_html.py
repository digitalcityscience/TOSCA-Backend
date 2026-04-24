"""
Legacy HTML → Editor.js block converter.

Used by the GeoContext preflight and (future) data migration to convert
legacy text- or HTML-valued content into the canonical Editor.js JSON
contract defined by :mod:`tosca_api.apps.core.editorjs`.

The converter is deterministic: identical input always produces byte-equal
output, so preflight and real migration agree on the conversion outcome.

Supported mappings
------------------

- ``<h1>``, ``<h2>``, ``<h3>``, ``<h4>`` → header block with matching level
- ``<p>`` (and orphan top-level text) → paragraph block, preserving inline
  ``<a>``, ``<strong>``, ``<em>``, ``<code>``, ``<br>`` markup
- ``<ul>`` / ``<ol>`` → list block; nested ``<ul>``/``<ol>`` are preserved
  inside the item's ``items`` array
- ``<blockquote>`` → quote block (caption empty)
- ``<pre><code>…</code></pre>`` or ``<pre>…</pre>`` → code block
- Inline ``<code>`` inside paragraphs / quotes remains inline
- ``<br>`` inside paragraphs / list items is preserved
- ``<b>`` and ``<i>`` are normalized to ``<strong>`` / ``<em>`` by the
  Editor.js validator during the final pass

Blocking content
----------------

Any of ``<img>``, ``<figure>``, or ``<figcaption>`` anywhere in the
document aborts conversion with :class:`LegacyHtmlMediaError`. This keeps
the feature explicit about rows that need manual review instead of
silently dropping media references.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from tosca_api.apps.core.editorjs import validate_and_normalize
from tosca_api.apps.core.sanitization import sanitize_rich

_BLOCKING_TAGS = {"img", "figure", "figcaption"}
_HEADER_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
_BLOCK_TAGS = set(_HEADER_TAGS) | {"p", "ul", "ol", "blockquote", "pre"}
_INLINE_TAGS = {"strong", "em", "b", "i", "code", "a", "br"}


class LegacyHtmlMediaError(ValueError):
    """Raised when legacy HTML contains blocking media markup."""

    def __init__(self, tags: list[str]) -> None:
        self.tags = sorted(set(tags))
        super().__init__(
            f"Legacy HTML contains blocking tags: {', '.join(self.tags)}"
        )


def _detect_blocking_tags(html: str) -> list[str]:
    """Return sorted blocking tags found anywhere in ``html``."""

    class _Detector(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.hits: set[str] = set()

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            if tag in _BLOCKING_TAGS:
                self.hits.add(tag)

        def handle_startendtag(self, tag: str, attrs: Any) -> None:
            if tag in _BLOCKING_TAGS:
                self.hits.add(tag)

    detector = _Detector()
    detector.feed(html or "")
    return sorted(detector.hits)


class _BlockBuilder(HTMLParser):
    """Parse sanitized HTML into canonical Editor.js blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        # Stack of in-progress blocks; top is the active block being filled.
        self._stack: list[dict] = []
        # Text buffer for orphan text nodes outside any known block.
        self._orphan: list[str] = []
        # Stack of list contexts — each entry is a dict with style/items.
        self._list_stack: list[dict] = []
        # Stack of inline-open tags that we re-emit verbatim.
        self._inline_stack: list[str] = []
        # Whether we are inside <pre>; content is captured verbatim.
        self._in_pre = False
        self._pre_buf: list[str] = []

    # ------------------------------------------------------------------
    # Orphan folding
    # ------------------------------------------------------------------

    def _flush_orphan(self) -> None:
        text = "".join(self._orphan).strip()
        self._orphan = []
        if text:
            self.blocks.append({"type": "paragraph", "data": {"text": text}})

    # ------------------------------------------------------------------
    # Inline helpers
    # ------------------------------------------------------------------

    def _render_inline_open(self, tag: str, attrs: list) -> str:
        if tag == "a":
            href = next((v for (k, v) in attrs if k == "href" and v), "")
            title = next((v for (k, v) in attrs if k == "title" and v), "")
            href_attr = f' href="{href}"' if href else ""
            title_attr = f' title="{title}"' if title else ""
            return f"<a{href_attr}{title_attr}>"
        if tag == "br":
            return "<br>"
        return f"<{tag}>"

    def _render_inline_close(self, tag: str) -> str:
        if tag == "br":
            return ""
        return f"</{tag}>"

    def _append_to_active(self, fragment: str) -> None:
        """Append a text/HTML fragment to the currently active context."""
        if self._in_pre:
            self._pre_buf.append(fragment)
            return
        if self._list_stack:
            # Append to the current list item's content buffer.
            current = self._list_stack[-1]
            if current["items"] and "content" in current["items"][-1]:
                current["items"][-1]["content"] += fragment
                return
        if self._stack:
            data = self._stack[-1]["data"]
            if "text" in data:
                data["text"] += fragment
                return
            if "code" in data:
                data["code"] += fragment
                return
        self._orphan.append(fragment)

    # ------------------------------------------------------------------
    # HTMLParser hooks
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _INLINE_TAGS:
            # Inside <pre>, drop inline tag markup; we only want the raw
            # text that the code block displays.
            if self._in_pre:
                return
            self._inline_stack.append(tag)
            self._append_to_active(self._render_inline_open(tag, attrs))
            return

        if tag == "pre":
            self._flush_orphan()
            self._in_pre = True
            self._pre_buf = []
            return

        if tag in _HEADER_TAGS:
            self._flush_orphan()
            self._stack.append(
                {"type": "header", "data": {"text": "", "level": _HEADER_TAGS[tag]}}
            )
            return

        if tag == "p":
            self._flush_orphan()
            self._stack.append({"type": "paragraph", "data": {"text": ""}})
            return

        if tag == "blockquote":
            self._flush_orphan()
            self._stack.append(
                {"type": "quote", "data": {"text": "", "caption": ""}}
            )
            return

        if tag in ("ul", "ol"):
            if not self._list_stack:
                self._flush_orphan()
            style = "ordered" if tag == "ol" else "unordered"
            self._list_stack.append({"style": style, "items": []})
            return

        if tag == "li":
            if self._list_stack:
                self._list_stack[-1]["items"].append({"content": "", "items": []})
            return

        # Unknown non-media tag — skip silently, content inside still handled.

    def handle_endtag(self, tag: str) -> None:
        if tag in _INLINE_TAGS:
            if self._in_pre:
                return
            if self._inline_stack and self._inline_stack[-1] == tag:
                self._inline_stack.pop()
            self._append_to_active(self._render_inline_close(tag))
            return

        if tag == "pre":
            code_text = "".join(self._pre_buf)
            # Strip a single leading/trailing newline that authors often
            # add inside <pre>…</pre> for readability.
            code_text = re.sub(r"^\n", "", code_text)
            code_text = re.sub(r"\n$", "", code_text)
            self.blocks.append({"type": "code", "data": {"code": code_text}})
            self._in_pre = False
            self._pre_buf = []
            return

        if tag in _HEADER_TAGS or tag == "p" or tag == "blockquote":
            if self._stack:
                self.blocks.append(self._stack.pop())
            return

        if tag in ("ul", "ol"):
            if not self._list_stack:
                return
            finished = self._list_stack.pop()
            if self._list_stack:
                # Nested list — attach to the parent list's last item.
                parent_items = self._list_stack[-1]["items"]
                if parent_items:
                    parent_items[-1]["items"].extend(
                        _convert_items_to_nested(finished["items"], finished["style"])
                    )
            else:
                self.blocks.append(
                    {
                        "type": "list",
                        "data": {
                            "style": finished["style"],
                            "items": finished["items"],
                            "meta": {},
                        },
                    }
                )
            return

        if tag == "li":
            return

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag == "br":
            if self._in_pre:
                self._pre_buf.append("\n")
                return
            self._append_to_active("<br>")
            return
        # Self-closing unknown tags: ignore.

    def handle_data(self, data: str) -> None:
        if self._in_pre:
            self._pre_buf.append(data)
            return
        # Collapse internal whitespace runs but preserve intentional spacing.
        self._append_to_active(data)

    def close(self) -> None:
        super().close()
        self._flush_orphan()


def _convert_items_to_nested(items: list[dict], style: str) -> list[dict]:
    """
    Legacy nested lists in HTML are inlined into the parent list's last
    item. The two Editor.js list styles are recorded at the outer block
    only, so nested items simply carry ``content`` + ``items`` recursively.
    """
    return [dict(i) for i in items]


def convert_legacy_html(html: str) -> dict:
    """
    Convert legacy text / HTML to a canonical Editor.js document.

    - Blank input → ``{"blocks": []}``
    - Plain text (no HTML tags) → a single paragraph block
    - Media-bearing HTML → :class:`LegacyHtmlMediaError`
    - Otherwise → sanitized HTML → parsed block JSON → validated by
      :func:`tosca_api.apps.core.editorjs.validate_and_normalize`

    The output is always a canonical Editor.js document ready for storage
    in ``GeoContext.content``.
    """
    if html is None:
        return {"blocks": []}

    raw = str(html)
    if not raw.strip():
        return {"blocks": []}

    blocking = _detect_blocking_tags(raw)
    if blocking:
        raise LegacyHtmlMediaError(blocking)

    # If the input contains no block-level tags, short-circuit to a single
    # paragraph. This is the legacy ``simple`` content path.
    lowered = raw.lower()
    has_block_markup = any(f"<{tag}" in lowered for tag in _BLOCK_TAGS)
    if not has_block_markup:
        stripped = sanitize_rich(raw).strip()
        if not stripped:
            return {"blocks": []}
        return validate_and_normalize(
            {"blocks": [{"type": "paragraph", "data": {"text": stripped}}]}
        )

    sanitized = sanitize_rich(raw)
    builder = _BlockBuilder()
    builder.feed(sanitized)
    builder.close()
    return validate_and_normalize({"blocks": builder.blocks})
