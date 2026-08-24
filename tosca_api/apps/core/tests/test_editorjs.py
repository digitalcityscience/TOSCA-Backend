"""
Tests for the Editor.js validation and normalization layer.
"""

import io

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from PIL import Image

from tosca_api.apps.core.editorjs import (
    description_document_from_text,
    description_document_to_text,
    empty_document,
    validate_and_normalize,
    validate_description_document,
)


def _png_bytes(width: int = 300, height: int = 300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def stored_image() -> str:
    """Save a real PNG via default_storage and yield the public media URL."""
    saved_paths: list[str] = []

    def _save(name: str = "editorjs-test.png", *, width: int = 300, height: int = 300) -> str:
        path = default_storage.save(
            f"geocontext/editorjs/test/{name}",
            ContentFile(_png_bytes(width, height)),
        )
        saved_paths.append(path)
        media_url = settings.MEDIA_URL if settings.MEDIA_URL.endswith("/") else settings.MEDIA_URL + "/"
        return f"{media_url}{path}"

    yield _save

    for path in saved_paths:
        default_storage.delete(path)


def test_empty_inputs_return_canonical_empty_document():
    for value in (None, "", {}, [], {"blocks": []}):
        assert validate_and_normalize(value) == {"blocks": []}


def test_non_dict_input_rejected():
    with pytest.raises(ValidationError):
        validate_and_normalize("hello")


def test_missing_blocks_key_rejected():
    with pytest.raises(ValidationError):
        validate_and_normalize({"time": 1, "version": "2.28.0"})


def test_accepts_supported_block_types():
    doc = {
        "blocks": [
            {"type": "paragraph", "data": {"text": "Hello"}},
            {"type": "header", "data": {"text": "T", "level": 2}},
            {"type": "list", "data": {"style": "unordered", "items": ["a", "b"]}},
            {"type": "quote", "data": {"text": "q", "caption": "c"}},
            {"type": "delimiter", "data": {}},
            {"type": "code", "data": {"code": "print(1)"}},
        ]
    }
    out = validate_and_normalize(doc)
    types = [b["type"] for b in out["blocks"]]
    assert types == ["paragraph", "header", "list", "quote", "delimiter", "code"]


def test_normalizes_save_envelope_strips_time_version_and_block_ids():
    doc = {
        "time": 1234567890,
        "version": "2.28.0",
        "blocks": [
            {
                "id": "abc123",
                "type": "paragraph",
                "data": {"text": "Hi"},
                "tunes": {"ignored": True},
            }
        ],
    }
    out = validate_and_normalize(doc)
    assert out == {"blocks": [{"type": "paragraph", "data": {"text": "Hi"}}]}
    assert "time" not in out
    assert "version" not in out
    assert "id" not in out["blocks"][0]


def test_normalizes_b_and_i_to_semantic_tags():
    doc = {
        "blocks": [
            {"type": "paragraph", "data": {"text": "<b>bold</b> and <i>italic</i>"}},
        ]
    }
    out = validate_and_normalize(doc)
    text = out["blocks"][0]["data"]["text"]
    assert "<strong>bold</strong>" in text
    assert "<em>italic</em>" in text
    assert "<b>" not in text and "<i>" not in text


def test_strips_disallowed_tags_like_script_and_handlers():
    doc = {
        "blocks": [
            {"type": "paragraph", "data": {"text": "safe<script>alert(1)</script>"}},
        ]
    }
    out = validate_and_normalize(doc)
    text = out["blocks"][0]["data"]["text"]
    assert "<script" not in text
    assert "alert" not in text
    assert text.startswith("safe")


def test_rejects_javascript_url_scheme_in_links():
    doc = {
        "blocks": [
            {"type": "paragraph", "data": {"text": '<a href="javascript:alert(1)">x</a>'}},
        ]
    }
    with pytest.raises(ValidationError):
        validate_and_normalize(doc)


def test_accepts_safe_url_schemes_in_links():
    doc = {
        "blocks": [
            {"type": "paragraph", "data": {"text": '<a href="https://x.test">x</a>'}},
        ]
    }
    out = validate_and_normalize(doc)
    assert 'href="https://x.test"' in out["blocks"][0]["data"]["text"]


def test_rejects_unsupported_block_type():
    with pytest.raises(ValidationError):
        validate_and_normalize({"blocks": [{"type": "checklist", "data": {"items": []}}]})


def test_rejects_header_level_out_of_range():
    for bad_level in (0, 5, "2", None):
        with pytest.raises(ValidationError):
            validate_and_normalize(
                {"blocks": [{"type": "header", "data": {"text": "t", "level": bad_level}}]}
            )


def test_rejects_quote_alignment():
    with pytest.raises(ValidationError):
        validate_and_normalize(
            {"blocks": [{"type": "quote", "data": {"text": "q", "caption": "c", "alignment": "left"}}]}
        )


def test_accepts_ordered_list_meta_from_editorjs():
    doc = {
        "blocks": [
            {
                "type": "list",
                "data": {
                    "style": "ordered",
                    "items": ["a"],
                    "meta": {"start": 5, "counterType": "lower-roman"},
                },
            }
        ]
    }
    out = validate_and_normalize(doc)
    assert out["blocks"][0]["data"]["meta"] == {
        "start": 5,
        "counterType": "lower-roman",
    }


def test_rejects_non_empty_unordered_list_meta():
    with pytest.raises(ValidationError):
        validate_and_normalize(
            {
                "blocks": [
                    {
                        "type": "list",
                        "data": {
                            "style": "unordered",
                            "items": ["a"],
                            "meta": {"start": 5},
                        },
                    }
                ]
            }
        )


def test_rejects_invalid_ordered_list_meta():
    cases = [
        {"start": 0},
        {"start": True},
        {"counterType": "emoji"},
        {"foo": "bar"},
    ]
    for meta in cases:
        with pytest.raises(ValidationError):
            validate_and_normalize(
                {
                    "blocks": [
                        {
                            "type": "list",
                            "data": {
                                "style": "ordered",
                                "items": ["a"],
                                "meta": meta,
                            },
                        }
                    ]
                }
            )


def test_accepts_nested_list_items():
    doc = {
        "blocks": [
            {
                "type": "list",
                "data": {
                    "style": "unordered",
                    "items": [
                        {"content": "parent", "items": [{"content": "child", "items": []}]},
                    ],
                    "meta": {},
                },
            }
        ]
    }
    out = validate_and_normalize(doc)
    items = out["blocks"][0]["data"]["items"]
    assert items[0]["content"] == "parent"
    assert items[0]["items"][0]["content"] == "child"


def test_list_string_items_are_upgraded_to_content_dicts():
    doc = {
        "blocks": [
            {"type": "list", "data": {"style": "ordered", "items": ["a", "b"], "meta": {}}}
        ]
    }
    out = validate_and_normalize(doc)
    items = out["blocks"][0]["data"]["items"]
    assert items == [
        {"content": "a", "items": []},
        {"content": "b", "items": []},
    ]


def test_description_profile_projects_supported_rich_content_to_plain_text():
    document = {
        "blocks": [
            {"type": "header", "data": {"text": "<strong>Overview</strong>", "level": 2}},
            {"type": "paragraph", "data": {"text": "First<br>second"}},
            {
                "type": "list",
                "data": {
                    "style": "ordered",
                    "items": [
                        {
                            "content": '<a href="https://example.test">Linked item</a>',
                            "items": [{"content": "Nested item", "items": []}],
                        }
                    ],
                    "meta": {},
                },
            },
        ]
    }

    normalized = validate_description_document(document)

    assert description_document_to_text(normalized) == (
        "Overview\n\nFirst\nsecond\n\n1. Linked item\n  - Nested item"
    )


def test_description_profile_rejects_full_editor_only_blocks_and_h1():
    with pytest.raises(ValidationError):
        validate_description_document(
            {"blocks": [{"type": "quote", "data": {"text": "No", "caption": ""}}]}
        )
    with pytest.raises(ValidationError):
        validate_description_document(
            {"blocks": [{"type": "header", "data": {"text": "No", "level": 1}}]}
        )


def test_plain_text_description_is_preserved_as_paragraph_blocks():
    document = description_document_from_text("First line\nsecond line\n\nAnother paragraph")

    assert document == {
        "blocks": [
            {"type": "paragraph", "data": {"text": "First line<br>second line"}},
            {"type": "paragraph", "data": {"text": "Another paragraph"}},
        ]
    }
    assert description_document_to_text(document) == (
        "First line\nsecond line\n\nAnother paragraph"
    )


def test_invalid_block_shape_rejected():
    with pytest.raises(ValidationError):
        validate_and_normalize({"blocks": ["not-a-dict"]})
    with pytest.raises(ValidationError):
        validate_and_normalize({"blocks": [{"type": "paragraph"}]})


def test_round_trip_is_byte_equal():
    doc = {
        "blocks": [
            {"type": "paragraph", "data": {"text": "Hello <strong>world</strong>"}},
            {"type": "header", "data": {"text": "Title", "level": 3}},
            {"type": "list", "data": {"style": "ordered", "items": ["x"], "meta": {}}},
            {"type": "delimiter", "data": {}},
            {"type": "code", "data": {"code": "x=1"}},
        ]
    }
    once = validate_and_normalize(doc)
    twice = validate_and_normalize(once)
    assert once == twice


def test_empty_document_helper():
    assert empty_document() == {"blocks": []}


# ---- image block ----------------------------------------------------------


@pytest.mark.django_db
def test_image_block_normalizes_with_server_derived_metadata(stored_image):
    url = stored_image(width=320, height=240)
    doc = {
        "blocks": [
            {
                "type": "image",
                "data": {
                    "file": {"url": url, "width": 9999, "height": 9999, "mime": "image/gif", "extra": "drop"},
                    "alt": "A sample picture",
                    "caption": "<strong>Hello</strong><script>x</script>",
                    "withBorder": True,
                    "withBackground": "truthy",
                    "stretched": False,
                    "unknown": "drop",
                },
            }
        ]
    }
    out = validate_and_normalize(doc)
    block = out["blocks"][0]
    assert block["type"] == "image"
    data = block["data"]
    assert data["file"] == {
        "url": url,
        "mime": "image/png",
        "width": 320,
        "height": 240,
    }
    assert data["alt"] == "A sample picture"
    assert "<strong>Hello</strong>" in data["caption"]
    assert "<script" not in data["caption"]
    assert data["withBorder"] is True
    assert data["withBackground"] is True
    assert data["stretched"] is False
    assert "unknown" not in data


@pytest.mark.django_db
def test_image_block_accepts_storage_generated_url():
    """The URL a real upload returns (``storage.url(name)``) must validate.

    ``stored_image`` hand-builds a ``MEDIA_URL``-relative path, which always
    satisfies the filesystem-backend contract regardless of what backend is
    actually configured. Under the S3 backend, ``storage.url(name)`` instead
    returns a presigned URL shaped ``{endpoint}/{bucket}/{location}/{name}?
    {signature}`` -- this must also validate, or every real image upload
    saved through the admin/API fails with "must be a storage URL under
    '/media/'" even though the file exists and was just uploaded.
    """
    path = default_storage.save(
        "geocontext/editorjs/test/storage-url.png", ContentFile(_png_bytes(200, 100))
    )
    try:
        url = default_storage.url(path)
        out = validate_and_normalize(
            {"blocks": [{"type": "image", "data": {"file": {"url": url}, "alt": "x"}}]}
        )
    finally:
        default_storage.delete(path)

    data = out["blocks"][0]["data"]
    assert data["file"]["url"] == url
    assert data["file"]["width"] == 200
    assert data["file"]["height"] == 100


@pytest.mark.django_db
def test_image_block_rejects_non_storage_paths_and_unsafe_schemes(stored_image):
    cases = [
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "https://evil.example.com/foo.png",
    ]
    for bad in cases:
        with pytest.raises(ValidationError):
            validate_and_normalize(
                {
                    "blocks": [
                        {
                            "type": "image",
                            "data": {"file": {"url": bad}, "alt": "x"},
                        }
                    ]
                }
            )


def test_image_block_allows_absolute_media_url(stored_image):
    with override_settings(MEDIA_URL="https://gq2.dcs.hcu-hamburg.de/media/"):
        url = stored_image(width=320, height=240)
        out = validate_and_normalize(
            {"blocks": [{"type": "image", "data": {"file": {"url": url}, "alt": "x"}}]}
        )

    assert out["blocks"][0]["data"]["file"]["url"] == url


@pytest.mark.django_db
def test_image_block_requires_alt_or_caption(stored_image):
    url = stored_image()
    # Both alt and caption empty -> reject.
    with pytest.raises(ValidationError):
        validate_and_normalize(
            {"blocks": [{"type": "image", "data": {"file": {"url": url}, "alt": "   "}}]}
        )
    with pytest.raises(ValidationError):
        validate_and_normalize(
            {"blocks": [{"type": "image", "data": {"file": {"url": url}}}]}
        )


@pytest.mark.django_db
def test_image_block_falls_back_to_caption_for_alt(stored_image):
    url = stored_image()
    out = validate_and_normalize(
        {
            "blocks": [
                {
                    "type": "image",
                    "data": {
                        "file": {"url": url},
                        "caption": "Sunset over the <strong>harbor</strong>",
                    },
                }
            ]
        }
    )
    block_data = out["blocks"][0]["data"]
    assert block_data["alt"] == "Sunset over the harbor"
    assert "<strong>harbor</strong>" in block_data["caption"]


@pytest.mark.django_db
def test_image_block_requires_existing_file():
    fake_url = settings.MEDIA_URL + "geocontext/editorjs/test/missing.png"
    with pytest.raises(ValidationError):
        validate_and_normalize(
            {"blocks": [{"type": "image", "data": {"file": {"url": fake_url}, "alt": "x"}}]}
        )


# ---- image block count ----------------------------------------------------


@pytest.mark.django_db
def test_image_blocks_are_not_capped(stored_image):
    url = stored_image()
    doc = {
        "blocks": [
            {"type": "image", "data": {"file": {"url": url}, "alt": "a"}}
            for _ in range(6)
        ]
    }
    normalized = validate_and_normalize(doc)
    assert sum(1 for block in normalized["blocks"] if block["type"] == "image") == 6
