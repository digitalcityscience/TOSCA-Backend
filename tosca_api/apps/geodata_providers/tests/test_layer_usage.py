"""Tests for Layer.usage_summary() and the Layer destroy pre-delete guard."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geodata_providers.api.views import LayerViewSet


def _call_destroy(user, layer, *, confirm: bool = False):
    """Invoke LayerViewSet.destroy directly without needing a URL mount."""
    factory = APIRequestFactory()
    url = f"/?confirm=true" if confirm else "/"
    request = factory.delete(url)
    force_authenticate(request, user=user)
    view = LayerViewSet.as_view({"delete": "destroy"})
    return view(request, pk=str(layer.id))
from tosca_api.apps.events.models import Event, EventLayer
from tosca_api.apps.feedback.models import FeedbackLayer, GeoFeedback
from tosca_api.apps.geodata_providers.models import Layer
from tosca_api.apps.geodata_providers.test_helpers import make_layer
from tosca_api.apps.geostories.models import GeoStory, GeoStoryLayer

User = get_user_model()


@pytest.fixture
def admin_user():
    user = User.objects.create_user(
        username="usage_admin", password="x", is_staff=True, is_superuser=True
    )
    # security tickets ticket 11 (A9): LayerViewSet.destroy now goes through
    # WorkspaceOwnedScopedPermission, not satisfied by a bare Django
    # `is_superuser` flag alone (ticket 07's fix) -- attach platform-exempt
    # claims so this pre-existing fixture's user still authorizes.
    user._auth_claims = AuthClaims(
        org_roles={}, default_org=None, authoritative=True, platform_exempt=True,
    )
    return user


@pytest.fixture
def campaign(admin_user):
    return Campaign.objects.create(title="Usage Campaign", created_by=admin_user)


@pytest.fixture
def layer(admin_user):
    return make_layer("workspace:usage_layer", user=admin_user)


@pytest.mark.django_db
def test_usage_summary_zero_for_unreferenced_layer(layer):
    assert layer.usage_summary() == {
        "geostories": 0,
        "events": 0,
        "feedbacks": 0,
    }


@pytest.mark.django_db
def test_usage_summary_counts_each_consumer(layer, admin_user, campaign):
    story = GeoStory.objects.create(
        title="S", campaign=campaign, author=admin_user
    )
    GeoStoryLayer.objects.create(geostory=story, layer=layer)

    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="E",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=admin_user,
    )
    EventLayer.objects.create(event=event, layer=layer)

    fb = GeoFeedback.objects.create(
        campaign=campaign,
        title="F",
        rating_enabled=True,
        form_enabled=False,
        created_by=admin_user,
    )
    FeedbackLayer.objects.create(feedback=fb, layer=layer)

    assert layer.usage_summary() == {
        "geostories": 1,
        "events": 1,
        "feedbacks": 1,
    }


@pytest.mark.django_db
def test_destroy_returns_409_when_layer_in_use(
    admin_user, layer, campaign, monkeypatch
):
    """API destroy without ?confirm=true must return 409 with usage."""
    story = GeoStory.objects.create(
        title="S", campaign=campaign, author=admin_user
    )
    GeoStoryLayer.objects.create(geostory=story, layer=layer)

    # Block any real engine call — we should not reach the service.
    from tosca_api.apps.geodata_providers.services.commands import layer_service

    monkeypatch.setattr(
        layer_service.LayerService,
        "delete_layer_safe",
        lambda *a, **kw: pytest.fail("delete_layer_safe should not run on 409"),
    )

    resp = _call_destroy(admin_user, layer)
    assert resp.status_code == 409
    assert resp.data["error"] == "layer_in_use"
    assert resp.data["usage"]["geostories"] == 1
    assert Layer.objects.filter(id=layer.id).exists()


@pytest.mark.django_db
def test_destroy_with_confirm_true_runs_delete(
    admin_user, layer, campaign, monkeypatch
):
    """With ?confirm=true the destroy proceeds to LayerService.delete_layer_safe."""
    story = GeoStory.objects.create(
        title="S", campaign=campaign, author=admin_user
    )
    GeoStoryLayer.objects.create(geostory=story, layer=layer)

    from tosca_api.apps.geodata_providers.services.commands import layer_service

    called = {}

    def fake_delete(layer_obj):
        called["called"] = True
        layer_obj.delete()
        return {"success": True}

    monkeypatch.setattr(
        layer_service.LayerService, "delete_layer_safe", fake_delete
    )

    resp = _call_destroy(admin_user, layer, confirm=True)
    assert resp.status_code == 200
    assert called.get("called") is True
    assert resp.data["usage"]["geostories"] == 1
    assert not Layer.objects.filter(id=layer.id).exists()


@pytest.mark.django_db
def test_destroy_unreferenced_layer_does_not_require_confirm(
    admin_user, layer, monkeypatch
):
    from tosca_api.apps.geodata_providers.services.commands import layer_service

    def fake_delete(layer_obj):
        layer_obj.delete()
        return {"success": True}

    monkeypatch.setattr(
        layer_service.LayerService, "delete_layer_safe", fake_delete
    )

    resp = _call_destroy(admin_user, layer)
    assert resp.status_code == 200
    assert not Layer.objects.filter(id=layer.id).exists()
