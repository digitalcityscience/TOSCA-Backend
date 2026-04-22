"""
Tests for the Editor.js validation and normalization layer.
"""

import pytest
from django.core.exceptions import ValidationError

from tosca_api.apps.core.editorjs import empty_document, validate_and_normalize


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
        validate_and_normalize({"blocks": [{"type": "image", "data": {"url": "x"}}]})


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


def test_rejects_non_empty_list_meta():
    with pytest.raises(ValidationError):
        validate_and_normalize(
            {
                "blocks": [
                    {
                        "type": "list",
                        "data": {
                            "style": "ordered",
                            "items": ["a"],
                            "meta": {"start": 5},
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
