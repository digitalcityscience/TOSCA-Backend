"""
Editor.js validation and normalization layer for GeoContext content.

This module owns block-schema validation, deterministic normalization, and
inline HTML sanitization for Editor.js documents stored in
``GeoContext.content``. It is intentionally separate from the legacy HTML
sanitizer in :mod:`tosca_api.apps.core.sanitization`, which serves freeform
text fields on other models.

Accepted input: either the canonical storage shape ``{"blocks": [...]}`` or
a full Editor.js save envelope ``{"time", "version", "blocks"}``. The
envelope fields and any per-block ``id`` are stripped on normalization so
stored documents are deterministic and round-trip stable.

MVP block set: ``paragraph``, ``header``, ``list``, ``quote``, ``delimiter``,
``code``.

MVP inline toolset: ``a``, ``strong``, ``em``, ``code``, ``br``. ``<b>`` and
``<i>`` are rewritten to ``<strong>`` and ``<em>`` respectively. Unsafe URL
schemes (anything outside ``http``, ``https``, ``mailto``) are rejected.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import nh3
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage

_ALLOWED_BLOCK_TYPES = {
    "paragraph",
    "header",
    "list",
    "quote",
    "delimiter",
    "code",
    "image",
}

_PILLOW_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}

_INLINE_TAGS = {"a", "strong", "em", "code", "br"}
_INLINE_ATTRS = {"a": {"href", "title"}}
_INLINE_URL_SCHEMES = {"http", "https", "mailto"}

_HEADER_LEVELS = {1, 2, 3, 4}
_LIST_STYLES = {"ordered", "unordered"}

_B_OPEN = re.compile(r"<b(\s[^>]*)?>", re.IGNORECASE)
_B_CLOSE = re.compile(r"</b\s*>", re.IGNORECASE)
_I_OPEN = re.compile(r"<i(\s[^>]*)?>", re.IGNORECASE)
_I_CLOSE = re.compile(r"</i\s*>", re.IGNORECASE)

_UNSAFE_SCHEME = re.compile(r"""<a\b[^>]*\bhref\s*=\s*['"]?\s*([a-zA-Z][a-zA-Z0-9+.\-]*):""")


def empty_document() -> dict:
    """Return the canonical empty Editor.js document."""
    return {"blocks": []}


def validate_and_normalize(value: Any) -> dict:
    """
    Validate and normalize an Editor.js document.

    Accepts ``None`` / ``{}`` / ``{"blocks": []}`` / full save envelope and
    returns the canonical storage form ``{"blocks": [...]}`` with only
    supported block types and inline formatting.

    Raises:
        ValidationError: if the document shape or any block is invalid.
    """
    if value in (None, "", {}, []):
        return empty_document()

    if not isinstance(value, dict):
        raise ValidationError("GeoContext content must be a JSON object.")

    if "blocks" not in value:
        raise ValidationError("GeoContext content must contain a 'blocks' array.")

    blocks = value["blocks"]
    if not isinstance(blocks, list):
        raise ValidationError("'blocks' must be an array.")

    normalized_blocks = [_normalize_block(b, idx) for idx, b in enumerate(blocks)]
    return {"blocks": normalized_blocks}


def _normalize_block(block: Any, idx: int) -> dict:
    if not isinstance(block, dict):
        raise ValidationError(f"Block at index {idx} must be an object.")

    block_type = block.get("type")
    if block_type not in _ALLOWED_BLOCK_TYPES:
        raise ValidationError(
            f"Block at index {idx} has unsupported type '{block_type}'. "
            f"Allowed types: {sorted(_ALLOWED_BLOCK_TYPES)}."
        )

    data = block.get("data")
    if not isinstance(data, dict):
        raise ValidationError(f"Block at index {idx} is missing a 'data' object.")

    normalizer = _BLOCK_NORMALIZERS[block_type]
    normalized_data = normalizer(data, idx)
    return {"type": block_type, "data": normalized_data}


def _normalize_paragraph(data: dict, idx: int) -> dict:
    text = data.get("text", "")
    if not isinstance(text, str):
        raise ValidationError(f"paragraph block at {idx} requires a string 'text'.")
    return {"text": _sanitize_inline(text, idx)}


def _normalize_header(data: dict, idx: int) -> dict:
    text = data.get("text", "")
    level = data.get("level")
    if not isinstance(text, str):
        raise ValidationError(f"header block at {idx} requires a string 'text'.")
    if level not in _HEADER_LEVELS:
        raise ValidationError(
            f"header block at {idx} requires 'level' in {sorted(_HEADER_LEVELS)}, got {level!r}."
        )
    return {"text": _sanitize_inline(text, idx), "level": level}


def _normalize_list(data: dict, idx: int) -> dict:
    style = data.get("style")
    if style not in _LIST_STYLES:
        raise ValidationError(
            f"list block at {idx} requires 'style' in {sorted(_LIST_STYLES)}, got {style!r}."
        )
    items = data.get("items")
    if not isinstance(items, list):
        raise ValidationError(f"list block at {idx} requires an 'items' array.")
    meta = data.get("meta", {})
    if meta != {}:
        raise ValidationError(
            f"list block at {idx} must have 'meta' equal to {{}} (MVP); got {meta!r}."
        )
    normalized_items = [_normalize_list_item(item, idx, path=str(i)) for i, item in enumerate(items)]
    return {"style": style, "items": normalized_items, "meta": {}}


def _normalize_list_item(item: Any, block_idx: int, path: str) -> dict:
    if isinstance(item, str):
        return {"content": _sanitize_inline(item, block_idx), "items": []}
    if isinstance(item, dict):
        content = item.get("content", "")
        if not isinstance(content, str):
            raise ValidationError(
                f"list item {path} in block {block_idx} requires a string 'content'."
            )
        nested = item.get("items", [])
        if not isinstance(nested, list):
            raise ValidationError(
                f"list item {path} in block {block_idx} 'items' must be an array."
            )
        return {
            "content": _sanitize_inline(content, block_idx),
            "items": [
                _normalize_list_item(child, block_idx, path=f"{path}.{i}")
                for i, child in enumerate(nested)
            ],
        }
    raise ValidationError(
        f"list item {path} in block {block_idx} must be a string or object."
    )


def _normalize_quote(data: dict, idx: int) -> dict:
    if "alignment" in data:
        raise ValidationError(f"quote block at {idx} must not set 'alignment' (MVP).")
    text = data.get("text", "")
    caption = data.get("caption", "")
    if not isinstance(text, str) or not isinstance(caption, str):
        raise ValidationError(f"quote block at {idx} requires string 'text' and 'caption'.")
    return {"text": _sanitize_inline(text, idx), "caption": _sanitize_inline(caption, idx)}


def _normalize_delimiter(data: dict, idx: int) -> dict:
    return {}


def _normalize_code(data: dict, idx: int) -> dict:
    code = data.get("code", "")
    if not isinstance(code, str):
        raise ValidationError(f"code block at {idx} requires a string 'code'.")
    return {"code": code}


def _media_url_prefix() -> str:
    media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
    if not media_url.endswith("/"):
        media_url += "/"
    return media_url


def _resolve_same_origin_storage_path(url: str, idx: int) -> str:
    """Reject off-origin / unsafe URLs, return the storage-relative path."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        raise ValidationError(
            f"image block at {idx} rejects URL scheme '{parsed.scheme}:'."
        )
    media_url = _media_url_prefix()
    path = parsed.path
    if not path.startswith(media_url):
        raise ValidationError(
            f"image block at {idx} 'data.file.url' must be a same-origin "
            f"storage URL under '{media_url}'."
        )
    return path[len(media_url):]


def _read_storage_image_metadata(storage_path: str, idx: int) -> dict:
    """Open the file via Django storage and derive (mime, width, height)."""
    if not default_storage.exists(storage_path):
        raise ValidationError(
            f"image block at {idx} references missing file '{storage_path}'."
        )
    from PIL import Image, UnidentifiedImageError

    try:
        with default_storage.open(storage_path, "rb") as fh:
            with Image.open(fh) as img:
                fmt = (img.format or "").upper()
                width, height = img.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError(
            f"image block at {idx} failed to decode '{storage_path}': {exc}"
        ) from exc

    mime = _PILLOW_FORMAT_TO_MIME.get(fmt)
    if mime is None:
        raise ValidationError(
            f"image block at {idx} stored file has unsupported format '{fmt or 'unknown'}'."
        )
    return {"mime": mime, "width": int(width), "height": int(height)}


def _normalize_image(data: dict, idx: int) -> dict:
    file_in = data.get("file")
    if not isinstance(file_in, dict):
        raise ValidationError(
            f"image block at {idx} requires a 'data.file' object."
        )

    url = file_in.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValidationError(
            f"image block at {idx} requires a non-empty 'data.file.url'."
        )

    storage_path = _resolve_same_origin_storage_path(url, idx)
    derived = _read_storage_image_metadata(storage_path, idx)

    caption_raw = data.get("caption", "")
    if not isinstance(caption_raw, str):
        raise ValidationError(
            f"image block at {idx} 'data.caption' must be a string."
        )

    # Alt is required for accessibility, but the upstream @editorjs/image
    # tool exposes only a caption field. Fall back to the caption (stripped
    # of all tags) when an explicit data.alt is not provided.
    alt_raw = data.get("alt")
    if alt_raw is not None and not isinstance(alt_raw, str):
        raise ValidationError(
            f"image block at {idx} 'data.alt' must be a string when set."
        )
    alt_source = alt_raw if alt_raw else caption_raw
    alt_clean = nh3.clean(alt_source or "", tags=set(), attributes={}).strip()
    if not alt_clean:
        raise ValidationError(
            f"image block at {idx} requires non-empty 'data.alt' or 'data.caption' text."
        )

    return {
        "file": {
            "url": url,
            "mime": derived["mime"],
            "width": derived["width"],
            "height": derived["height"],
        },
        "caption": _sanitize_inline(caption_raw, idx),
        "alt": alt_clean,
        "withBorder": bool(data.get("withBorder", False)),
        "withBackground": bool(data.get("withBackground", False)),
        "stretched": bool(data.get("stretched", False)),
    }


_BLOCK_NORMALIZERS = {
    "paragraph": _normalize_paragraph,
    "header": _normalize_header,
    "list": _normalize_list,
    "quote": _normalize_quote,
    "delimiter": _normalize_delimiter,
    "code": _normalize_code,
    "image": _normalize_image,
}


def _sanitize_inline(text: str, block_idx: int) -> str:
    """Normalize <b>/<i> to <strong>/<em>, strip disallowed tags, reject unsafe URLs."""
    if not text:
        return ""

    rewritten = _B_OPEN.sub("<strong>", text)
    rewritten = _B_CLOSE.sub("</strong>", rewritten)
    rewritten = _I_OPEN.sub("<em>", rewritten)
    rewritten = _I_CLOSE.sub("</em>", rewritten)

    for match in _UNSAFE_SCHEME.finditer(rewritten):
        scheme = match.group(1).lower()
        if scheme not in _INLINE_URL_SCHEMES:
            raise ValidationError(
                f"block {block_idx} contains unsafe URL scheme '{scheme}:' in inline link."
            )

    return nh3.clean(
        rewritten,
        tags=_INLINE_TAGS,
        attributes=_INLINE_ATTRS,
        url_schemes=_INLINE_URL_SCHEMES,
        link_rel=None,
    )
