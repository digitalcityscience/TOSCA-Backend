"""
Editor.js validation and normalization for feature-owned content.

This module owns block-schema validation, deterministic normalization, and
inline HTML sanitization for Editor.js documents stored in
feature content fields. It is intentionally separate from the legacy HTML
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

import copy
import html
import re
from typing import Any
from urllib.parse import urlparse

import nh3
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import InvalidStorageError, default_storage, storages

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
_DESCRIPTION_HEADER_LEVELS = {2, 3, 4}
_DESCRIPTION_BLOCK_TYPES = {"paragraph", "header", "list"}
_LIST_STYLES = {"ordered", "unordered"}
_ORDERED_LIST_COUNTER_TYPES = {
    "numeric",
    "lower-roman",
    "upper-roman",
    "lower-alpha",
    "upper-alpha",
}

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
        raise ValidationError("Content must be a JSON object.")

    if "blocks" not in value:
        raise ValidationError("Content must contain a 'blocks' array.")

    blocks = value["blocks"]
    if not isinstance(blocks, list):
        raise ValidationError("'blocks' must be an array.")

    normalized_blocks = [_normalize_block(b, idx) for idx, b in enumerate(blocks)]
    return {"blocks": normalized_blocks}


def validate_description_document(value: Any) -> dict:
    """Validate the deliberately small Editor.js profile used for descriptions."""
    document = validate_and_normalize(value)
    for index, block in enumerate(document["blocks"]):
        block_type = block["type"]
        if block_type not in _DESCRIPTION_BLOCK_TYPES:
            raise ValidationError(
                f"Description block at index {index} has unsupported type '{block_type}'. "
                "Use paragraphs, headings, or lists."
            )
        if block_type == "header" and block["data"]["level"] not in _DESCRIPTION_HEADER_LEVELS:
            raise ValidationError("Description headings must use levels 2, 3, or 4.")
    return document


def description_document_from_text(value: str | None) -> dict:
    """Convert legacy plain text into safe paragraph blocks without losing line breaks."""
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return empty_document()
    paragraphs = re.split(r"\n\s*\n+", text)
    return {
        "blocks": [
            {
                "type": "paragraph",
                "data": {
                    "text": "<br>".join(
                        html.escape(line, quote=False) for line in paragraph.split("\n")
                    ),
                },
            }
            for paragraph in paragraphs
            if paragraph.strip()
        ],
    }


def description_document_to_text(value: Any) -> str:
    """Return a deterministic plain-text projection for search and provider metadata."""
    document = validate_description_document(value)
    chunks: list[str] = []
    for block in document["blocks"]:
        block_type = block["type"]
        data = block["data"]
        if block_type in {"paragraph", "header"}:
            chunks.append(_inline_to_plain_text(data.get("text", "")))
        elif block_type == "list":
            chunks.append(_list_to_plain_text(data))
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


def _inline_to_plain_text(value: str) -> str:
    with_breaks = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    return html.unescape(nh3.clean(with_breaks, tags=set(), attributes={})).strip()


def _list_to_plain_text(data: dict) -> str:
    ordered = data.get("style") == "ordered"
    lines: list[str] = []

    def append_items(items: list, depth: int = 0) -> None:
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            marker = f"{index + 1}." if ordered and depth == 0 else "-"
            content = _inline_to_plain_text(str(item.get("content") or ""))
            lines.append(f"{'  ' * depth}{marker} {content}".rstrip())
            append_items(item.get("items") or [], depth + 1)

    append_items(data.get("items") or [])
    return "\n".join(lines)


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
    normalized_meta = _normalize_list_meta(meta, style, idx)
    normalized_items = [
        _normalize_list_item(item, idx, path=str(i)) for i, item in enumerate(items)
    ]
    return {"style": style, "items": normalized_items, "meta": normalized_meta}


def _normalize_list_meta(meta: Any, style: str, idx: int) -> dict:
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ValidationError(f"list block at {idx} 'meta' must be an object.")
    if style == "unordered":
        if meta != {}:
            raise ValidationError(
                f"unordered list block at {idx} must have empty 'meta'; got {meta!r}."
            )
        return {}

    normalized: dict[str, Any] = {}
    start = meta.get("start")
    if start is not None:
        if not isinstance(start, int) or isinstance(start, bool) or start < 1:
            raise ValidationError(
                f"ordered list block at {idx} 'meta.start' must be a positive integer."
            )
        normalized["start"] = start

    counter_type = meta.get("counterType")
    if counter_type is not None:
        if counter_type not in _ORDERED_LIST_COUNTER_TYPES:
            raise ValidationError(
                f"ordered list block at {idx} 'meta.counterType' must be in "
                f"{sorted(_ORDERED_LIST_COUNTER_TYPES)}, got {counter_type!r}."
            )
        normalized["counterType"] = counter_type

    unknown = set(meta) - {"start", "counterType"}
    if unknown:
        raise ValidationError(
            f"ordered list block at {idx} has unsupported 'meta' keys: {sorted(unknown)}."
        )
    return normalized


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
    raise ValidationError(f"list item {path} in block {block_idx} must be a string or object.")


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


def _media_url_path_prefix() -> str:
    media_url = _media_url_prefix()
    parsed = urlparse(media_url)
    path = parsed.path or media_url
    if not path.startswith("/"):
        path = f"/{path}"
    if not path.endswith("/"):
        path += "/"
    return path


def _canonical_media_url(storage_path: str) -> str:
    """Return the bucket-independent URL persisted in Editor.js content.

    Storage-generated S3 URLs are signed and name a particular bucket. Both
    properties make them unsuitable for durable JSON: signatures expire and
    the media lifecycle moves objects between buckets. Persist a stable media
    path instead and mint a current storage URL only when content is rendered.
    """
    return f"{_media_url_path_prefix()}{storage_path.lstrip('/')}"


def _s3_storage_and_key(parsed) -> tuple[Any, str] | None:
    """Match a parsed URL against a configured S3 storage alias' bucket.

    Under the S3 backend, ``storage.url(name)`` returns a presigned URL
    shaped ``{endpoint}/{bucket}/{location}/{name}?{signature}`` (path-style
    addressing) rather than a ``MEDIA_URL``-relative path, and the object may
    live in any of the three buckets (default/media_public/media_archive)
    depending on the owning entity's publication state. Recover ``(storage,
    name)`` by checking each configured alias' bucket/location against the
    URL's path.
    """
    for alias in ("default", "media_public", "media_archive"):
        try:
            storage = storages[alias]
        except InvalidStorageError:
            continue
        bucket_name = getattr(storage, "bucket_name", None)
        if not bucket_name:
            continue
        prefix = f"/{bucket_name}/"
        if not parsed.path.startswith(prefix):
            continue
        key = parsed.path[len(prefix) :]
        location = (getattr(storage, "location", "") or "").strip("/")
        if location and key.startswith(f"{location}/"):
            key = key[len(location) + 1 :]
        return storage, key
    return None


def _storage_reference(url: str, idx: int) -> tuple[Any | None, str]:
    """Reject unsafe/non-storage URLs and extract their storage path."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        raise ValidationError(f"image block at {idx} rejects URL scheme '{parsed.scheme}:'.")

    matched = _s3_storage_and_key(parsed)
    if matched is not None:
        return matched

    media_url = _media_url_path_prefix()
    path = parsed.path
    if not path.startswith(media_url):
        raise ValidationError(
            f"image block at {idx} 'data.file.url' must be a storage URL under '{media_url}'."
        )
    return None, path[len(media_url) :]


def _resolve_storage(url: str, idx: int) -> tuple[Any, str]:
    """Return the current storage backend and storage-relative path."""
    explicit_storage, storage_path = _storage_reference(url, idx)
    if explicit_storage is not None and explicit_storage.exists(storage_path):
        return explicit_storage, storage_path
    if default_storage.exists(storage_path):
        return default_storage, storage_path
    # A canonical media URL deliberately carries no bucket name. Resolve the
    # current bucket from MediaAsset so the same saved document remains valid
    # after publish/unpublish/archive lifecycle moves.
    from tosca_api.apps.core.models import MediaAsset

    asset = MediaAsset.objects.filter(storage_path=storage_path).only("storage_alias").first()
    storage = storages[asset.storage_alias] if asset is not None else default_storage
    return storage, storage_path


def render_content_media_urls(content: dict, request=None) -> dict:
    """Copy content and replace canonical/legacy image URLs with fresh URLs.

    Legacy rows may contain an expired signed URL or a URL for a bucket the
    lifecycle has since moved away from. The MediaAsset row is authoritative
    for both the storage path and current alias.
    """
    rendered = copy.deepcopy(content or {"blocks": []})
    references: list[tuple[dict, str]] = []
    for idx, block in enumerate(rendered.get("blocks") or []):
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        file_data = (block.get("data") or {}).get("file")
        if not isinstance(file_data, dict):
            continue
        url = file_data.get("url")
        if not isinstance(url, str) or not url:
            continue
        try:
            _, storage_path = _storage_reference(url, idx)
        except ValidationError:
            continue
        references.append((file_data, storage_path))

    if not references:
        return rendered

    from tosca_api.apps.core.models import MediaAsset

    assets = {
        asset.storage_path: asset
        for asset in MediaAsset.objects.filter(
            storage_path__in={storage_path for _, storage_path in references}
        ).only("storage_path", "storage_alias")
    }
    for file_data, storage_path in references:
        asset = assets.get(storage_path)
        if asset is None:
            continue
        fresh_url = storages[asset.storage_alias].url(storage_path)
        file_data["url"] = (
            request.build_absolute_uri(fresh_url) if request is not None else fresh_url
        )
    return rendered


def _read_storage_image_metadata(storage: Any, storage_path: str, idx: int) -> dict:
    """Open the file via Django storage and derive (mime, width, height)."""
    if not storage.exists(storage_path):
        raise ValidationError(f"image block at {idx} references missing file '{storage_path}'.")
    from PIL import Image, UnidentifiedImageError

    try:
        with storage.open(storage_path, "rb") as fh:
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
        raise ValidationError(f"image block at {idx} requires a 'data.file' object.")

    url = file_in.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValidationError(f"image block at {idx} requires a non-empty 'data.file.url'.")

    storage, storage_path = _resolve_storage(url, idx)
    derived = _read_storage_image_metadata(storage, storage_path, idx)

    caption_raw = data.get("caption", "")
    if not isinstance(caption_raw, str):
        raise ValidationError(f"image block at {idx} 'data.caption' must be a string.")

    # Alt is required for accessibility, but the upstream @editorjs/image
    # tool exposes only a caption field. Fall back to the caption (stripped
    # of all tags) when an explicit data.alt is not provided.
    alt_raw = data.get("alt")
    if alt_raw is not None and not isinstance(alt_raw, str):
        raise ValidationError(f"image block at {idx} 'data.alt' must be a string when set.")
    alt_source = alt_raw if alt_raw else caption_raw
    alt_clean = nh3.clean(alt_source or "", tags=set(), attributes={}).strip()
    if not alt_clean:
        raise ValidationError(
            f"image block at {idx} requires non-empty 'data.alt' or 'data.caption' text."
        )

    return {
        "file": {
            "url": _canonical_media_url(storage_path),
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
