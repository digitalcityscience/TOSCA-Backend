"""
Task 7.5 regression coverage: GeoContext JSON contract at read surfaces.

The original Phase 7 migration plan was to (1) add a parallel ``content_json``
field, (2) switch reads/writes to it while retaining legacy columns for
rollback, then (3) drop the legacy columns. Task 7.1 was executed
destructively instead — ``content_type`` and the legacy text field were
dropped outright and ``GeoContext.content`` is already the canonical
Editor.js JSON column. See ``docs/features-to-add/decisions.md`` §[7.5].

These tests therefore act as the final regression guard that every
application read surface which embeds GeoContext emits the canonical
``{"blocks": [...]}`` contract — and that no remaining code path depends
on the retired ``content_type`` discriminator.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import Event
from tosca_api.apps.feedback.models import GeoFeedback
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geostories.models import GeoStory

User = get_user_model()


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="json_contract_user", password="pw")


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        username="json_contract_admin", password="pw", email="a@a.test"
    )


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="JSON Contract", created_by=user)


@pytest.fixture
def rich_context(user):
    return GeoContext.objects.create(
        title="Rich Context Title",
        content={
            "blocks": [
                {"type": "header", "data": {"text": "Intro", "level": 2}},
                {"type": "paragraph", "data": {"text": "Hello"}},
            ]
        },
        created_by=user,
    )


@pytest.fixture
def empty_context(user):
    return GeoContext.objects.create(content={"blocks": []}, created_by=user)


# ----------------------------------------------------------------------
# Geostory detail
# ----------------------------------------------------------------------


@pytest.mark.django_db
def test_geostory_detail_reads_context_as_json_blocks(
    api_client, user, campaign, rich_context
):
    story = GeoStory.objects.create(
        title="Story",
        status=GeoStory.Status.PUBLISHED,
        campaign=campaign,
        author=user,
        context=rich_context,
    )
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/{story.id}/")
    assert response.status_code == 200

    ctx = response.data["context"]
    assert isinstance(ctx["content"], dict)
    assert "blocks" in ctx["content"]
    assert ctx["content"]["blocks"][0]["type"] == "header"
    # The retired legacy discriminator must not leak into the contract.
    assert "content_type" not in ctx


@pytest.mark.django_db
def test_geostory_detail_empty_context_serializes_as_empty_blocks(
    api_client, user, campaign, empty_context
):
    story = GeoStory.objects.create(
        title="Empty",
        status=GeoStory.Status.PUBLISHED,
        campaign=campaign,
        author=user,
        context=empty_context,
    )
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/{story.id}/")
    assert response.status_code == 200
    assert response.data["context"]["content"] == {"blocks": []}


# ----------------------------------------------------------------------
# Event detail
# ----------------------------------------------------------------------


@pytest.mark.django_db
def test_event_detail_reads_context_as_json_blocks(
    api_client, user, campaign, rich_context
):
    event = Event.objects.create(
        campaign=campaign,
        title="Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        context=rich_context,
    )
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/{event.id}/")
    assert response.status_code == 200

    ctx = response.data["context"]
    assert isinstance(ctx["content"], dict)
    assert ctx["content"]["blocks"][1]["data"]["text"] == "Hello"
    assert "content_type" not in ctx


@pytest.mark.django_db
def test_event_detail_without_context_returns_none(
    api_client, user, campaign
):
    event = Event.objects.create(
        campaign=campaign,
        title="No Context",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/{event.id}/")
    assert response.status_code == 200
    assert response.data["context"] is None


# ----------------------------------------------------------------------
# Feedback detail
# ----------------------------------------------------------------------


@pytest.mark.django_db
def test_feedback_detail_reads_context_as_json_blocks(
    api_client, admin_user, campaign, rich_context
):
    feedback = GeoFeedback.objects.create(
        campaign=campaign,
        title="FB",
        created_by=admin_user,
        context=rich_context,
        status=GeoFeedback.Status.PUBLISHED,
        visibility=GeoFeedback.Visibility.PUBLIC,
    )
    response = api_client.get(f"/api/v1/feedback/{feedback.id}/")
    assert response.status_code == 200

    ctx = response.data["context"]
    assert isinstance(ctx["content"], dict)
    assert ctx["content"]["blocks"][0]["type"] == "header"
    assert "content_type" not in ctx


@pytest.mark.django_db
def test_feedback_detail_empty_context_serializes_as_empty_blocks(
    api_client, admin_user, campaign, empty_context
):
    feedback = GeoFeedback.objects.create(
        campaign=campaign,
        title="FB Empty",
        created_by=admin_user,
        context=empty_context,
        status=GeoFeedback.Status.PUBLISHED,
        visibility=GeoFeedback.Visibility.PUBLIC,
    )
    response = api_client.get(f"/api/v1/feedback/{feedback.id}/")
    assert response.status_code == 200
    assert response.data["context"]["content"] == {"blocks": []}


# ----------------------------------------------------------------------
# Admin writes go through the canonical JSON path
# ----------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_edit_updates_context_as_json_blocks(client, admin_user):
    """An admin save persists the canonical JSON contract end-to-end."""
    from django.urls import reverse

    client.force_login(admin_user)
    url = reverse("admin:geocontext_geocontext_add")
    payload = {
        "content": (
            '{"blocks": [{"type": "paragraph", "data": {"text": "Admin wrote"}}]}'
        ),
        "created_by": str(admin_user.id),
        "_save": "Save",
    }
    response = client.post(url, payload, follow=True)
    assert response.status_code == 200
    ctx = GeoContext.objects.get(created_by=admin_user)
    assert ctx.content == {
        "blocks": [{"type": "paragraph", "data": {"text": "Admin wrote"}}]
    }


# ----------------------------------------------------------------------
# Regression: retired content_type discriminator has no active callers
# ----------------------------------------------------------------------


def test_no_module_imports_sanitize_content():
    """
    ``sanitize_content(content, content_type)`` was the Task 7.1 relic
    that dispatched on the retired discriminator. The helper has been
    removed; this test fails loud if anything re-adds a caller.
    """
    from tosca_api.apps.core import sanitization

    assert not hasattr(sanitization, "sanitize_content")


def test_geocontext_model_has_no_content_type_field():
    field_names = {f.name for f in GeoContext._meta.get_fields()}
    assert "content_type" not in field_names
    assert "content" in field_names
    assert "title" in field_names


# ----------------------------------------------------------------------
# Title propagation to nested read surfaces
# ----------------------------------------------------------------------


@pytest.mark.django_db
def test_geostory_detail_exposes_context_title(
    api_client, user, campaign, rich_context
):
    story = GeoStory.objects.create(
        title="Story",
        status=GeoStory.Status.PUBLISHED,
        campaign=campaign,
        author=user,
        context=rich_context,
    )
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/{story.id}/")
    assert response.status_code == 200
    assert response.data["context"]["title"] == "Rich Context Title"


@pytest.mark.django_db
def test_event_detail_exposes_context_title(
    api_client, user, campaign, rich_context
):
    event = Event.objects.create(
        campaign=campaign,
        title="Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        context=rich_context,
    )
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/{event.id}/")
    assert response.status_code == 200
    assert response.data["context"]["title"] == "Rich Context Title"


@pytest.mark.django_db
def test_feedback_detail_exposes_context_title(
    api_client, admin_user, campaign, rich_context
):
    feedback = GeoFeedback.objects.create(
        campaign=campaign,
        title="FB",
        created_by=admin_user,
        context=rich_context,
        status=GeoFeedback.Status.PUBLISHED,
        visibility=GeoFeedback.Visibility.PUBLIC,
    )
    response = api_client.get(f"/api/v1/feedback/{feedback.id}/")
    assert response.status_code == 200
    assert response.data["context"]["title"] == "Rich Context Title"
