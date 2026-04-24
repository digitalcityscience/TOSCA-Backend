"""
Tests for GeoContext model - canonical Editor.js JSON contract.
"""

import pytest
from django.contrib.auth import get_user_model

from tosca_api.apps.geocontext.models import GeoContext, empty_editorjs_document

User = get_user_model()


@pytest.fixture
def test_user(db):
    """Create a test user for geocontext ownership."""
    return User.objects.create_user(
        username="testuser_geocontext",
        password="testpass123",
    )


@pytest.mark.django_db
def test_geocontext_empty_defaults_to_empty_blocks(test_user):
    """A new GeoContext with no content resolves to {"blocks": []}."""
    ctx = GeoContext.objects.create(created_by=test_user)
    assert ctx.content == {"blocks": []}


@pytest.mark.django_db
def test_geocontext_persists_editorjs_json_content(test_user):
    """Valid Editor.js documents are persisted as JSON."""
    doc = {
        "blocks": [
            {"type": "paragraph", "data": {"text": "Hello"}},
            {"type": "header", "data": {"text": "Title", "level": 2}},
        ]
    }
    ctx = GeoContext.objects.create(content=doc, created_by=test_user)
    ctx.refresh_from_db()
    assert ctx.content == doc
    assert ctx.content["blocks"][0]["data"]["text"] == "Hello"


@pytest.mark.django_db
def test_geocontext_none_normalizes_to_empty_blocks(test_user):
    """Explicit None content is normalized to the canonical empty doc."""
    ctx = GeoContext(content=None, created_by=test_user)
    ctx.save()
    assert ctx.content == {"blocks": []}


@pytest.mark.django_db
def test_geocontext_missing_content_defaults_safely(test_user):
    """Creating without a content argument defaults to the canonical empty doc."""
    ctx = GeoContext.objects.create(created_by=test_user)
    assert ctx.content == empty_editorjs_document()


@pytest.mark.django_db
def test_geocontext_has_no_content_type_field():
    """The legacy content_type field must not exist on the model."""
    field_names = {f.name for f in GeoContext._meta.get_fields()}
    assert "content_type" not in field_names


@pytest.mark.django_db
def test_geocontext_str_prefers_explicit_title(test_user):
    """An explicit title wins over derived excerpt in __str__."""
    ctx = GeoContext.objects.create(
        title="Smart City Logistics",
        content={"blocks": [{"type": "paragraph", "data": {"text": "body"}}]},
        created_by=test_user,
    )
    label = str(ctx)
    assert label.startswith("Smart City Logistics")
    assert "1 block" in label


@pytest.mark.django_db
def test_geocontext_str_falls_back_to_first_block_excerpt(test_user):
    """Without a title, __str__ derives a short excerpt from the first block."""
    ctx = GeoContext.objects.create(
        content={
            "blocks": [
                {"type": "header", "data": {"text": "Quantum AI Overview", "level": 2}},
                {"type": "paragraph", "data": {"text": "body"}},
            ]
        },
        created_by=test_user,
    )
    label = str(ctx)
    assert "Quantum AI Overview" in label
    assert "2 block" in label


@pytest.mark.django_db
def test_geocontext_str_empty_document_is_labeled(test_user):
    """Empty docs still produce a dropdown-friendly label."""
    empty = GeoContext.objects.create(created_by=test_user)
    assert "(empty)" in str(empty)


@pytest.mark.django_db
def test_geocontext_str_no_title_no_text_falls_back_to_short_id(test_user):
    """A rich document with no text-bearing block still gets a stable label."""
    ctx = GeoContext.objects.create(
        content={"blocks": [{"type": "delimiter", "data": {}}]},
        created_by=test_user,
    )
    label = str(ctx)
    assert "GeoContext " in label
    assert "1 block" in label
