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
def test_geocontext_str_representation(test_user):
    """String representation reflects block count, not raw text."""
    empty = GeoContext.objects.create(created_by=test_user)
    assert "(empty)" in str(empty)

    populated = GeoContext.objects.create(
        content={"blocks": [{"type": "paragraph", "data": {"text": "Hi"}}]},
        created_by=test_user,
    )
    assert "1 block" in str(populated)
