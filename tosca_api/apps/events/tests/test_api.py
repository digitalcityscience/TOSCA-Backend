from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import (
    Event,
    EventSeries,
    EventTerm,
    EventType,
    TaxonomyDimension,
    TaxonomyTerm,
)
from tosca_api.apps.geocontext.models import GeoContext

User = get_user_model()


def build_series_kwargs(user, campaign, event_type, **overrides):
    # Anchor wallclock to mid-day so end_time stays after start_time even
    # when the suite runs late-evening UTC (where now.time()+1h wraps midnight).
    now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
    kwargs = {
        "campaign": campaign,
        "event_type": event_type,
        "created_by": user,
        "name": "Series",
        "series_mode": EventSeries.SeriesMode.MANUAL_BATCH,
        "start_date": now.date(),
        "start_time": now.time().replace(tzinfo=None, microsecond=0),
        "end_time": (now + timedelta(hours=1)).time().replace(
            tzinfo=None,
            microsecond=0,
        ),
        "timezone": "Europe/Berlin",
    }
    kwargs.update(overrides)
    return kwargs


def parse_api_datetime(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def create_published_event(user, campaign, **overrides):
    defaults = {
        "campaign": campaign,
        "title": "Published Event",
        "summary": "Published event summary",
        "start_datetime": timezone.now() + timedelta(days=2),
        "end_datetime": timezone.now() + timedelta(days=2, hours=1),
        "location": Point(10.0, 53.5, srid=4326),
        "organizer": user,
        "status": Event.Status.PUBLISHED,
        "visibility": Event.Visibility.PUBLIC,
        "provider_phone": "+49 89 12345",
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


@pytest.fixture
def api_client():
    return APIClient()


def _org_token(*roles, org="dcs"):
    """Keycloak-shaped token for org-scoped writes (epic-11 PR1 §3.3).

    ``CampaignScopedPermission``/``EventWriteSerializer.validate`` now
    require a WRITER+ role for the campaign's organization on any
    non-SAFE_METHODS request; ``campaign`` fixture below attaches to the
    conftest-seeded ``dcs`` org (see conftest.py's default-org save wrapper),
    so ``org="dcs"`` is the default here to match.
    """
    return {"realm_access": {"roles": list(roles)}, "default_organization": org}


def _authenticate_org_writer(api_client, user, *roles, org="dcs"):
    """Authenticate ``user`` for both gate C (``request.auth`` token, read by
    ``CampaignScopedPermission``) and gate A (``user._auth_claims``, read by
    ``has_perm()`` -> ``OrgRolePermissionBackend`` via
    ``DjangoModelPermissionsOrAnonReadOnly``, security tickets ticket 10).

    ``APIClient.force_authenticate`` bypasses ``KeycloakTokenAuthentication``
    entirely, so it never attaches ``_auth_claims`` itself -- it must be set
    on the same ``user`` object passed in here, since DRF's
    ``force_authenticate`` uses that exact instance as ``request.user``.
    """
    level = roles[0].rsplit("_", 1)[-1] if roles else None
    if level:
        user._auth_claims = AuthClaims(
            org_roles={org: level}, default_org=org, authoritative=True
        )
    api_client.force_authenticate(user=user, token=_org_token(*roles, org=org))


@pytest.fixture
def user():
    return User.objects.create_user(username="eventapiuser", password="password")


@pytest.fixture
def staff_user():
    return User.objects.create_user(
        username="staffuser", password="password", is_staff=True
    )


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="API Test Campaign", created_by=user)


@pytest.fixture
def geocontext(user):
    return GeoContext.objects.create(
        content={"blocks": [{"type": "paragraph", "data": {"text": "Shared API context"}}]},
        created_by=user,
    )


@pytest.fixture
def event_type():
    return EventType.objects.create(code="api-core", label="API Core Event")


# =============================================================================
# Public Registry Tests
# =============================================================================


@pytest.mark.django_db
def test_event_types_registry_returns_active_types_with_normalized_profile_key(
    api_client,
):
    core_type = EventType.objects.create(
        code="registry-core",
        label="Registry Core",
        profile_mode=EventType.ProfileMode.CORE,
        profile_key=None,
    )
    ph_type = EventType.objects.create(
        code="registry-ph",
        label="Registry Public Health",
        profile_mode=EventType.ProfileMode.EXTENSION,
        profile_key="public_health",
    )
    EventType.objects.create(
        code="registry-inactive",
        label="Registry Inactive",
        profile_mode=EventType.ProfileMode.CORE,
        is_active=False,
    )

    response = api_client.get("/api/v1/event-types/")

    assert response.status_code == 200
    by_code = {item["code"]: item for item in response.data}
    assert by_code["registry-core"]["id"] == str(core_type.id)
    assert by_code["registry-core"]["profile_key"] == ""
    assert by_code["registry-ph"]["id"] == str(ph_type.id)
    assert by_code["registry-ph"]["profile_key"] == "public_health"
    assert "registry-inactive" not in by_code


@pytest.mark.django_db
def test_event_taxonomy_registry_returns_compatible_active_dimensions_and_terms(
    api_client,
):
    global_dimension = TaxonomyDimension.objects.create(
        code="registry-global-topic",
        label="Registry Global Topic",
    )
    active_global_term = TaxonomyTerm.objects.create(
        dimension=global_dimension,
        code="registry-global-active",
        label="Registry Global Active",
    )
    TaxonomyTerm.objects.create(
        dimension=global_dimension,
        code="registry-global-inactive",
        label="Registry Global Inactive",
        is_active=False,
    )
    ph_dimension = TaxonomyDimension.objects.create(
        code="registry-ph-topic",
        label="Registry PH Topic",
        profile_key="public_health",
    )
    active_ph_term = TaxonomyTerm.objects.create(
        dimension=ph_dimension,
        code="registry-ph-active",
        label="Registry PH Active",
    )
    inactive_dimension = TaxonomyDimension.objects.create(
        code="registry-inactive-dimension",
        label="Registry Inactive Dimension",
        profile_key="public_health",
        is_active=False,
    )
    TaxonomyTerm.objects.create(
        dimension=inactive_dimension,
        code="registry-hidden-term",
        label="Registry Hidden Term",
    )

    response = api_client.get("/api/v1/event-taxonomy/?profile_key=public_health")

    assert response.status_code == 200
    assert response.data["profile_key"] == "public_health"
    by_code = {dimension["code"]: dimension for dimension in response.data["dimensions"]}
    assert "registry-global-topic" in by_code
    assert "registry-ph-topic" in by_code
    assert "registry-inactive-dimension" not in by_code
    assert by_code["registry-global-topic"]["terms"] == [
        {
            "id": str(active_global_term.id),
            "code": "registry-global-active",
            "label": "Registry Global Active",
            "parent_id": None,
            "is_active": True,
        }
    ]
    assert by_code["registry-ph-topic"]["terms"][0]["id"] == str(active_ph_term.id)


@pytest.mark.django_db
def test_event_taxonomy_registry_without_profile_returns_global_dimensions_only(
    api_client,
):
    TaxonomyDimension.objects.create(code="registry-core-only", label="Registry Core Only")
    TaxonomyDimension.objects.create(
        code="registry-ph-hidden",
        label="Registry PH Hidden",
        profile_key="public_health",
    )

    response = api_client.get("/api/v1/event-taxonomy/")

    assert response.status_code == 200
    by_code = {dimension["code"] for dimension in response.data["dimensions"]}
    assert "registry-core-only" in by_code
    assert "registry-ph-hidden" not in by_code


@pytest.fixture
def future_event(user, campaign):
    """Create a future published event with location."""
    return Event.objects.create(
        campaign=campaign,
        title="Future Event",
        summary="Future event summary",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=2),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        provider_phone="+49 89 12345",
    )


@pytest.fixture
def past_event(user, campaign):
    """Create a past published event."""
    return Event.objects.create(
        campaign=campaign,
        title="Past Event",
        summary="Past event summary",
        start_datetime=timezone.now() - timedelta(days=2),
        end_datetime=timezone.now() - timedelta(days=2, hours=-2),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        provider_phone="+49 89 12345",
    )


@pytest.fixture
def event_without_location(user, campaign):
    """Create an online event without geometry."""
    return Event.objects.create(
        campaign=campaign,
        title="No Location Event",
        summary="Online event summary",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
        online_platform="Zoom",
        location=None,
        organizer=user,
        status=Event.Status.PUBLISHED,
        provider_phone="+49 89 12345",
    )


@pytest.fixture
def draft_event(user, campaign):
    """Create a draft event."""
    return Event.objects.create(
        campaign=campaign,
        title="Draft Event",
        start_datetime=timezone.now() + timedelta(days=5),
        end_datetime=timezone.now() + timedelta(days=5, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.DRAFT,
    )


# =============================================================================
# Authentication Tests
# =============================================================================


@pytest.mark.django_db
def test_events_list_unauthenticated_is_public(api_client):
    """Reads are public; the list endpoint responds with 200 for anonymous clients."""
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 200


# =============================================================================
# Calendar View Tests (List)
# =============================================================================


@pytest.mark.django_db
def test_events_list_returns_future_only_by_default(
    api_client, user, future_event, past_event
):
    """Test that list returns only future events by default."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "Past Event" not in titles


@pytest.mark.django_db
def test_events_list_include_past(api_client, user, future_event, past_event):
    """Test that include_past=true returns past events."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/?include_past=true")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "Past Event" in titles


@pytest.mark.django_db
def test_events_list_includes_events_without_location(
    api_client, user, future_event, event_without_location
):
    """Test that calendar view includes events without location."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "No Location Event" in titles


@pytest.mark.django_db
def test_events_list_filters_by_published_status(
    api_client, user, future_event, draft_event
):
    """Test that list returns only published events by default."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "Draft Event" not in titles


@pytest.mark.django_db
def test_events_list_filter_by_campaign(api_client, user, campaign, future_event):
    """Test filtering by campaign_id."""
    # Create another campaign with event
    other_campaign = Campaign.objects.create(title="Other Campaign", created_by=user)
    Event.objects.create(
        campaign=other_campaign,
        title="Other Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.2, 53.6, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/?campaign_id={campaign.id}")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "Other Event" not in titles


@pytest.mark.django_db
def test_events_list_filter_by_event_type(api_client, user, campaign, event_type):
    """The shared list filter should support event_type_id."""
    other_event_type = EventType.objects.create(code="other-type", label="Other Type")
    Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Typed Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    Event.objects.create(
        campaign=campaign,
        event_type=other_event_type,
        title="Other Typed Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(10.1, 53.6, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/?event_type_id={event_type.id}")

    assert response.status_code == 200
    assert [event["title"] for event in response.data["results"]] == ["Typed Event"]


@pytest.mark.django_db
def test_events_list_and_map_filter_by_profile_key(api_client, user, campaign):
    ph_type = EventType.objects.create(
        code="filter-ph-type",
        label="Filter PH Type",
        profile_mode=EventType.ProfileMode.EXTENSION,
        profile_key="public_health",
    )
    core_type = EventType.objects.create(
        code="filter-core-type",
        label="Filter Core Type",
        profile_mode=EventType.ProfileMode.CORE,
    )
    create_published_event(
        user,
        campaign,
        event_type=ph_type,
        title="PH Filter Match",
    )
    create_published_event(
        user,
        campaign,
        event_type=core_type,
        title="Core Filter Miss",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
    )

    list_response = api_client.get("/api/v1/events/?profile_key=public_health")
    map_response = api_client.get("/api/v1/events/map/?profile_key=public_health")

    assert list_response.status_code == 200
    assert map_response.status_code == 200
    assert [event["title"] for event in list_response.data["results"]] == [
        "PH Filter Match"
    ]
    assert [
        feature["properties"]["title"]
        for feature in map_response.data["spatial_events"]["features"]
    ] == ["PH Filter Match"]


@pytest.mark.django_db
def test_events_list_and_map_filter_by_taxonomy_codes(api_client, user, campaign):
    dimension = TaxonomyDimension.objects.create(
        code="filter-field-of-action",
        label="Filter Field of Action",
    )
    sport = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="filter-sport",
        label="Filter Sport",
    )
    other_term = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="filter-culture",
        label="Filter Culture",
    )
    matching_event = create_published_event(user, campaign, title="Taxonomy Code Match")
    other_event = create_published_event(
        user,
        campaign,
        title="Taxonomy Code Miss",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
    )
    EventTerm.objects.create(event=matching_event, term=sport)
    EventTerm.objects.create(event=other_event, term=other_term)

    params = "?dimension_code=filter-field-of-action&term_code=filter-sport"
    list_response = api_client.get(f"/api/v1/events/{params}")
    map_response = api_client.get(f"/api/v1/events/map/{params}")

    assert list_response.status_code == 200
    assert map_response.status_code == 200
    assert [event["title"] for event in list_response.data["results"]] == [
        "Taxonomy Code Match"
    ]
    assert [
        feature["properties"]["title"]
        for feature in map_response.data["spatial_events"]["features"]
    ] == ["Taxonomy Code Match"]


@pytest.mark.django_db
def test_events_taxonomy_uuid_and_code_filter_mismatch_returns_empty(
    api_client, user, campaign
):
    matching_dimension = TaxonomyDimension.objects.create(
        code="filter-mismatch-matching",
        label="Filter Mismatch Matching",
    )
    other_dimension = TaxonomyDimension.objects.create(
        code="filter-mismatch-other",
        label="Filter Mismatch Other",
    )
    sport = TaxonomyTerm.objects.create(
        dimension=matching_dimension,
        code="filter-mismatch-sport",
        label="Filter Mismatch Sport",
    )
    event = create_published_event(user, campaign, title="Mismatch Hidden")
    EventTerm.objects.create(event=event, term=sport)

    params = (
        f"?dimension_id={other_dimension.id}"
        "&dimension_code=filter-mismatch-matching"
        "&term_code=filter-mismatch-sport"
    )
    list_response = api_client.get(f"/api/v1/events/{params}")
    map_response = api_client.get(f"/api/v1/events/map/{params}")

    assert list_response.status_code == 200
    assert map_response.status_code == 200
    assert list_response.data["results"] == []
    assert map_response.data["spatial_events"]["features"] == []


@pytest.mark.django_db
def test_events_list_and_map_include_profile_key_and_compact_taxonomy(
    api_client, user, campaign
):
    ph_type = EventType.objects.create(
        code="shape-ph-type",
        label="Shape PH Type",
        profile_mode=EventType.ProfileMode.EXTENSION,
        profile_key="public_health",
    )
    dimension = TaxonomyDimension.objects.create(
        code="shape-field-of-action",
        label="Shape Field of Action",
        profile_key="public_health",
    )
    sport = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="shape-sport",
        label="Shape Sport",
    )
    spatial_event = create_published_event(
        user,
        campaign,
        event_type=ph_type,
        title="Shape Spatial",
    )
    online_event = create_published_event(
        user,
        campaign,
        event_type=ph_type,
        title="Shape Online",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/shape",
        location=None,
    )
    EventTerm.objects.create(event=spatial_event, term=sport)
    EventTerm.objects.create(event=online_event, term=sport)

    list_response = api_client.get("/api/v1/events/?profile_key=public_health")
    map_response = api_client.get("/api/v1/events/map/?profile_key=public_health")

    assert list_response.status_code == 200
    assert map_response.status_code == 200
    compact_assignment = {
        "dimension_code": "shape-field-of-action",
        "dimension_label": "Shape Field of Action",
        "terms": [{"code": "shape-sport", "label": "Shape Sport"}],
    }
    list_item = list_response.data["results"][0]
    assert list_item["profile_key"] == "public_health"
    assert list_item["taxonomy_assignments"] == [compact_assignment]

    spatial_properties = map_response.data["spatial_events"]["features"][0]["properties"]
    assert spatial_properties["profile_key"] == "public_health"
    assert spatial_properties["taxonomy_assignments"] == [compact_assignment]

    online_item = map_response.data["online_events"][0]
    assert online_item["profile_key"] == "public_health"
    assert online_item["taxonomy_assignments"] == [compact_assignment]


# =============================================================================
# List API V2 Tests
# =============================================================================


@pytest.mark.django_db
def test_events_list_v2_returns_mixed_modes_ordered_by_start_datetime(
    api_client, user, campaign
):
    """The dedicated list endpoint should return a chronological mixed stream."""
    Event.objects.create(
        campaign=campaign,
        title="Physical Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location_mode=Event.LocationMode.PHYSICAL,
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    Event.objects.create(
        campaign=campaign,
        title="Online Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    Event.objects.create(
        campaign=campaign,
        title="Hybrid Event",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
        location_mode=Event.LocationMode.HYBRID,
        location=Point(10.1, 53.6, srid=4326),
        online_url="https://example.org/hybrid",
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/")

    assert response.status_code == 200
    titles = [event["title"] for event in response.data["results"]]
    assert titles == ["Online Event", "Physical Event", "Hybrid Event"]
    assert [event["location_mode"] for event in response.data["results"]] == [
        "online",
        "physical",
        "hybrid",
    ]


@pytest.mark.django_db
def test_events_list_v2_paginates_mixed_event_types(api_client, user, campaign):
    """Cursor pagination should work for the dedicated list endpoint."""
    for index in range(1, 22):
        mode = [
            Event.LocationMode.ONLINE,
            Event.LocationMode.PHYSICAL,
            Event.LocationMode.HYBRID,
        ][(index - 1) % 3]
        kwargs = {
            "campaign": campaign,
            "title": f"Event {index}",
            "start_datetime": timezone.now() + timedelta(days=index),
            "end_datetime": timezone.now() + timedelta(days=index, hours=1),
            "location_mode": mode,
            "organizer": user,
            "status": Event.Status.PUBLISHED,
        }
        if mode == Event.LocationMode.ONLINE:
            kwargs["online_url"] = "https://example.org/live"
        else:
            kwargs["location"] = Point(10.0 + index, 53.5 + index, srid=4326)
            if mode == Event.LocationMode.HYBRID:
                kwargs["online_url"] = "https://example.org/hybrid"
        Event.objects.create(**kwargs)

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/")

    assert response.status_code == 200
    assert len(response.data["results"]) == 20
    assert response.data["next"]


@pytest.mark.django_db
def test_events_list_ignores_bbox_query_param(api_client, user, campaign):
    """`bbox` on the list endpoint is accepted but does not change the result shape."""
    Event.objects.create(
        campaign=campaign,
        title="Inside Physical Event",
        summary="Inside",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        provider_phone="+49 89 12345",
    )
    Event.objects.create(
        campaign=campaign,
        title="Outside Physical Event",
        summary="Outside",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(13.4, 52.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        provider_phone="+49 89 12345",
    )
    Event.objects.create(
        campaign=campaign,
        title="Eligible Online Event",
        summary="Online",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
        organizer=user,
        status=Event.Status.PUBLISHED,
        provider_phone="+49 89 12345",
    )

    response = api_client.get("/api/v1/events/?bbox=9.5,53.0,10.5,54.0")
    assert response.status_code == 200
    # Plain paginated JSON, not GeoJSON. bbox is ignored on the list endpoint;
    # the spatial response lives at /events/map/.
    assert "results" in response.data
    assert "features" not in response.data
    titles = {item["title"] for item in response.data["results"]}
    assert titles == {
        "Inside Physical Event",
        "Outside Physical Event",
        "Eligible Online Event",
    }


@pytest.mark.django_db
def test_events_list_staff_visibility_private_filter(
    api_client, user, staff_user, campaign
):
    """Staff can narrow the list to private events via the visibility filter."""
    Event.objects.create(
        campaign=campaign,
        title="Private Physical Event",
        summary="Private",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PRIVATE,
        provider_phone="+49 89 12345",
    )
    Event.objects.create(
        campaign=campaign,
        title="Public Online Event",
        summary="Public",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        provider_phone="+49 89 12345",
    )

    api_client.force_authenticate(user=staff_user)
    response = api_client.get("/api/v1/events/?visibility=private")
    assert response.status_code == 200
    titles = [item["title"] for item in response.data["results"]]
    assert titles == ["Private Physical Event"]


# =============================================================================
# Map API V2 Tests
# =============================================================================


@pytest.mark.django_db
def test_events_map_v2_returns_geojson_and_online_buckets(api_client, user, campaign):
    """The dedicated map endpoint should split spatial and online results."""
    Event.objects.create(
        campaign=campaign,
        title="Physical Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    Event.objects.create(
        campaign=campaign,
        title="Online Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/map/")

    assert response.status_code == 200
    assert response.data["spatial_events"]["type"] == "FeatureCollection"
    assert "features" in response.data["spatial_events"]
    assert response.data["online_events"][0]["title"] == "Online Event"


@pytest.mark.django_db
def test_events_map_v2_filters_by_event_type(api_client, user, campaign, event_type):
    """The shared map filter should support event_type_id."""
    other_event_type = EventType.objects.create(code="map-other-type", label="Map Other")
    Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Matching Spatial Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location_mode=Event.LocationMode.PHYSICAL,
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    Event.objects.create(
        campaign=campaign,
        event_type=other_event_type,
        title="Filtered Spatial Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location_mode=Event.LocationMode.PHYSICAL,
        location=Point(10.2, 53.6, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/map/?event_type_id={event_type.id}")

    assert response.status_code == 200
    assert [
        feature["properties"]["title"]
        for feature in response.data["spatial_events"]["features"]
    ] == ["Matching Spatial Event"]


@pytest.mark.django_db
def test_events_map_v2_spatial_bucket_contains_only_mapped_physical_or_hybrid_events(
    api_client, user, campaign
):
    """The spatial bucket should contain only physical and hybrid events with geometry."""
    Event.objects.create(
        campaign=campaign,
        title="Physical Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location_mode=Event.LocationMode.PHYSICAL,
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    Event.objects.create(
        campaign=campaign,
        title="Hybrid Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location_mode=Event.LocationMode.HYBRID,
        location=Point(10.1, 53.6, srid=4326),
        online_url="https://example.org/hybrid",
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    Event.objects.create(
        campaign=campaign,
        title="Online Event",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/map/")

    assert response.status_code == 200
    spatial_titles = [
        feature["properties"]["title"]
        for feature in response.data["spatial_events"]["features"]
    ]
    assert spatial_titles == ["Physical Event", "Hybrid Event"]
    assert response.data["online_events"][0]["title"] == "Online Event"


@pytest.mark.django_db
def test_events_map_v2_area_filter_keeps_eligible_online_events_separately(
    api_client, user, campaign
):
    """Map filtering should keep online events in the separate online bucket."""
    Event.objects.create(
        campaign=campaign,
        title="Inside Physical Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    Event.objects.create(
        campaign=campaign,
        title="Outside Physical Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(13.4, 52.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    Event.objects.create(
        campaign=campaign,
        title="Eligible Online Event",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/map/?bbox=9.5,53.0,10.5,54.0")

    assert response.status_code == 200
    spatial_titles = [
        feature["properties"]["title"]
        for feature in response.data["spatial_events"]["features"]
    ]
    online_titles = [event["title"] for event in response.data["online_events"]]
    assert spatial_titles == ["Inside Physical Event"]
    assert online_titles == ["Eligible Online Event"]


@pytest.mark.django_db
def test_events_map_v2_empty_spatial_bucket_still_returns_feature_collection(
    api_client, user, campaign
):
    """An empty spatial bucket should still return valid GeoJSON structure."""
    Event.objects.create(
        campaign=campaign,
        title="Online Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/map/?bbox=9.5,53.0,10.5,54.0")

    assert response.status_code == 200
    assert response.data["spatial_events"]["type"] == "FeatureCollection"
    assert response.data["spatial_events"]["features"] == []
    assert response.data["online_events"][0]["title"] == "Online Event"


# =============================================================================
# Map View Tests (BBox)
# =============================================================================


@pytest.mark.django_db
def test_events_list_invalid_bbox_format(api_client, user):
    """Even though bbox is ignored on the list endpoint, malformed values still 400."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/?bbox=invalid")
    assert response.status_code == 400


# =============================================================================
# Map View Tests (Polygon - POST /within/)
# =============================================================================


@pytest.mark.django_db
def test_events_within_polygon(api_client, user, campaign):
    """Test POST /events/within/ with polygon filter."""
    # Create event inside polygon
    Event.objects.create(
        campaign=campaign,
        title="Inside Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    # Create event outside polygon
    Event.objects.create(
        campaign=campaign,
        title="Outside Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(5.0, 50.0, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/events/within/",
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[9.0, 53.0], [11.0, 53.0], [11.0, 54.0], [9.0, 54.0], [9.0, 53.0]]
                ],
            }
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["type"] == "FeatureCollection"

    titles = [f["properties"]["title"] for f in response.data["features"]]
    assert "Inside Event" in titles
    assert "Outside Event" not in titles


@pytest.mark.django_db
def test_events_within_keeps_online_events_after_spatial_filtering(
    api_client, user, future_event, event_without_location
):
    """Polygon filtering should still keep online events that match non-spatial filters."""
    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/events/within/",
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[9.0, 53.0], [11.0, 53.0], [11.0, 54.0], [9.0, 54.0], [9.0, 53.0]]
                ],
            }
        },
        format="json",
    )

    assert response.status_code == 200
    titles = [feature["properties"]["title"] for feature in response.data["features"]]
    assert "Future Event" in titles
    assert "No Location Event" in titles


@pytest.mark.django_db
def test_events_within_excludes_past_events_by_default(api_client, user, campaign):
    """The shared filter layer should exclude past events in within/ by default."""
    Event.objects.create(
        campaign=campaign,
        title="Past Inside Event",
        start_datetime=timezone.now() - timedelta(days=2),
        end_datetime=timezone.now() - timedelta(days=2, hours=-1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/events/within/",
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[9.0, 53.0], [11.0, 53.0], [11.0, 54.0], [9.0, 54.0], [9.0, 53.0]]
                ],
            }
        },
        format="json",
    )

    assert response.status_code == 200
    titles = [feature["properties"]["title"] for feature in response.data["features"]]
    assert "Past Inside Event" not in titles


@pytest.mark.django_db
def test_events_shared_filters_match_between_list_and_within(api_client, user, staff_user, campaign):
    """List and within endpoints should apply the same non-spatial filter contract."""
    dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    climate = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="climate",
        label="Climate",
    )
    matching_event = Event.objects.create(
        campaign=campaign,
        title="Matching Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PRIVATE,
    )
    filtered_out_online = Event.objects.create(
        campaign=campaign,
        title="Filtered Out Online Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
    )
    EventTerm.objects.create(event=matching_event, term=climate)
    EventTerm.objects.create(event=filtered_out_online, term=climate)

    start_after = (timezone.now() + timedelta(days=1)).isoformat()
    start_before = (timezone.now() + timedelta(days=3)).isoformat()

    api_client.force_authenticate(user=staff_user)
    list_response = api_client.get(
        "/api/v1/events/",
        {
            "campaign_id": str(campaign.id),
            "status": Event.Status.PUBLISHED,
            "visibility": Event.Visibility.PRIVATE,
            "term_id": str(climate.id),
            "start_after": start_after,
            "start_before": start_before,
            "include_past": "false",
        },
    )
    within_response = api_client.post(
        "/api/v1/events/within/",
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[9.0, 53.0], [11.0, 53.0], [11.0, 54.0], [9.0, 54.0], [9.0, 53.0]]
                ],
            },
            "campaign_id": str(campaign.id),
            "status": Event.Status.PUBLISHED,
            "visibility": Event.Visibility.PRIVATE,
            "term_id": str(climate.id),
            "start_after": start_after,
            "start_before": start_before,
            "include_past": False,
        },
        format="json",
    )

    assert list_response.status_code == 200
    assert within_response.status_code == 200

    list_titles = [event["title"] for event in list_response.data["results"]]
    within_titles = [feature["properties"]["title"] for feature in within_response.data["features"]]
    assert list_titles == ["Matching Event"]
    assert within_titles == ["Matching Event"]


@pytest.mark.django_db
def test_events_within_rejects_non_polygon(api_client, user):
    """Test that within/ rejects Point geometry."""
    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/events/within/",
        {
            "geometry": {
                "type": "Point",
                "coordinates": [10.0, 53.5],
            }
        },
        format="json",
    )
    assert response.status_code == 400
    assert "Polygon" in str(response.data)


@pytest.mark.django_db
def test_events_within_invalid_geojson(api_client, user):
    """Test that invalid GeoJSON returns 400."""
    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/events/within/",
        {"geometry": {"type": "invalid", "coordinates": []}},
        format="json",
    )
    assert response.status_code == 400


# =============================================================================
# Detail View Tests
# =============================================================================


@pytest.mark.django_db
def test_events_retrieve_detail(api_client, user, future_event):
    """Test retrieving a single event returns full details."""
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/{future_event.id}/")
    assert response.status_code == 200

    assert response.data["id"] == str(future_event.id)
    assert response.data["title"] == "Future Event"
    assert "layers" in response.data
    assert "context" in response.data
    assert "taxonomy_assignments" in response.data


@pytest.mark.django_db
def test_events_retrieve_detail_uses_series_default_context(
    api_client, user, campaign, geocontext, event_type
):
    """Detail responses should expose the resolved series default context."""
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="API Series",
        ),
        default_context=geocontext,
    )
    event = Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Series Detail Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=2),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        series=series,
        occurrence_index=1,
        context=None,
        status=Event.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/{event.id}/")
    assert response.status_code == 200
    assert response.data["context"]["id"] == str(geocontext.id)


@pytest.mark.django_db
def test_events_retrieve_detail_returns_grouped_taxonomy_assignments(
    api_client, user, campaign, event_type
):
    """Detail responses should return grouped taxonomy assignments."""
    topic_dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    audience_dimension = TaxonomyDimension.objects.create(
        code="audience",
        label="Audience",
        selection_mode=TaxonomyDimension.SelectionMode.SINGLE,
    )
    climate = TaxonomyTerm.objects.create(
        dimension=topic_dimension,
        code="climate",
        label="Climate",
    )
    youth = TaxonomyTerm.objects.create(
        dimension=audience_dimension,
        code="youth",
        label="Youth",
    )
    event = Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Taxonomy Detail Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    EventTerm.objects.create(event=event, term=climate)
    EventTerm.objects.create(event=event, term=youth)

    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/{event.id}/")

    assert response.status_code == 200
    assignments = response.data["taxonomy_assignments"]
    assert len(assignments) == 2
    assert {
        assignment["dimension_code"]: set(assignment["term_ids"])
        for assignment in assignments
    } == {
        "topic": {str(climate.id)},
        "audience": {str(youth.id)},
    }


@pytest.mark.django_db
def test_event_series_retrieve_returns_grouped_taxonomy_assignments(
    api_client, user, staff_user, campaign, event_type
):
    """Series retrieve should derive grouped taxonomy assignments from the base occurrence."""
    dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    climate = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="climate",
        label="Climate",
    )

    _authenticate_org_writer(api_client, staff_user, "ROLE_DCS_WRITER")
    create_response = api_client.post(
        "/api/v1/event-series/",
        {
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "Retrieve Series",
            "series_mode": "recurring",
            "recurrence_type": "weekly",
            "start_date": "2026-04-06",
            "occurrence_count": 2,
            "interval": 1,
            "start_time": "18:00:00",
            "end_time": "19:00:00",
            "timezone": "Europe/Berlin",
            "by_weekday": ["monday"],
            "title": "Retrieve Event",
            "location_mode": "online",
            "online_url": "https://example.org/live",
            "status": "draft",
            "taxonomy_assignments": [
                {"dimension_id": str(dimension.id), "term_ids": [str(climate.id)]}
            ],
        },
        format="json",
    )
    series_id = create_response.data["id"]

    response = api_client.get(f"/api/v1/event-series/{series_id}/")

    assert response.status_code == 200
    assert response.data["taxonomy_assignments"][0]["dimension_code"] == "topic"
    assert response.data["taxonomy_assignments"][0]["term_ids"] == [str(climate.id)]


@pytest.mark.django_db
def test_event_series_retrieve_fails_without_base_occurrence_template(
    api_client, user, staff_user, campaign, event_type
):
    """Series retrieve should fail clearly when no base occurrence/template exists.

    Uses a staff user because empty series are filtered out of the non-staff
    queryset (no visible occurrence) and would otherwise return 404.
    """
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Legacy Empty Series",
        )
    )

    api_client.force_authenticate(user=staff_user)
    response = api_client.get(f"/api/v1/event-series/{series.id}/")

    assert response.status_code == 409
    assert "base occurrence/template" in response.data["detail"]


# =============================================================================
# Create/Update/Delete Tests
# =============================================================================


@pytest.mark.django_db
def test_event_series_preview_manual_batch_returns_one_occurrence_per_explicit_date(
    api_client, user, campaign, event_type
):
    """Manual batch preview should return one preview occurrence per explicit date."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.post(
        "/api/v1/event-series/preview/",
        {
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "Manual Batch",
            "series_mode": "manual_batch",
            "start_date": "2026-04-01",
            "start_time": "09:00:00",
            "end_time": "10:30:00",
            "timezone": "Europe/Berlin",
            "explicit_dates": ["2026-04-03", "2026-04-10", "2026-04-17"],
            "title": "Manual Event",
            "location_mode": "online",
            "online_url": "https://example.org/live",
            "status": "draft",
        },
        format="json",
    )

    assert response.status_code == 200
    assert [item["occurrence_index"] for item in response.data["occurrences"]] == [1, 2, 3]
    assert [item["occurrence_date"] for item in response.data["occurrences"]] == [
        "2026-04-03",
        "2026-04-10",
        "2026-04-17",
    ]


@pytest.mark.django_db
def test_event_series_preview_rejects_invalid_location_geojson(
    api_client, user, campaign, event_type
):
    """Series preview should return a clear location validation error for bad GeoJSON."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.post(
        "/api/v1/event-series/preview/",
        {
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "Bad Location Series",
            "series_mode": "manual_batch",
            "start_date": "2026-04-01",
            "start_time": "09:00:00",
            "end_time": "10:30:00",
            "timezone": "Europe/Berlin",
            "explicit_dates": ["2026-04-03"],
            "title": "Physical Event",
            "location_mode": "physical",
            "location": {"type": "LineString", "coordinates": [[10.0, 53.5], [10.1, 53.6]]},
            "status": "draft",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["location"] == ["Location must be a GeoJSON Point."]


@pytest.mark.django_db
def test_event_series_create_weekly_generates_occurrences_with_series_metadata(
    api_client, user, campaign, event_type
):
    """Recurring series creation should persist generated events with series metadata."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.post(
        "/api/v1/event-series/",
        {
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "Weekly Series",
            "series_mode": "recurring",
            "recurrence_type": "weekly",
            "start_date": "2026-04-06",
            "occurrence_count": 3,
            "interval": 1,
            "start_time": "18:00:00",
            "end_time": "19:30:00",
            "timezone": "Europe/Berlin",
            "by_weekday": ["monday"],
            "title": "Recurring Event",
            "location_mode": "online",
            "online_url": "https://example.org/live",
            "status": "draft",
        },
        format="json",
    )

    assert response.status_code == 201

    series = EventSeries.objects.get(id=response.data["id"])
    events = list(series.events.order_by("occurrence_index"))

    assert [event.occurrence_index for event in events] == [1, 2, 3]
    assert all(event.series_id == series.id for event in events)
    assert all(event.original_start_datetime == event.start_datetime for event in events)
    assert [event.start_datetime.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat() for event in events] == [
        "2026-04-06",
        "2026-04-13",
        "2026-04-20",
    ]


@pytest.mark.django_db
def test_event_series_preview_weekly_preserves_wall_time_across_dst(
    api_client, user, campaign, event_type
):
    """Weekly previews should preserve the same local clock time across DST changes."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.post(
        "/api/v1/event-series/preview/",
        {
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "DST Weekly Series",
            "series_mode": "recurring",
            "recurrence_type": "weekly",
            "start_date": "2026-03-23",
            "occurrence_count": 3,
            "interval": 1,
            "start_time": "09:00:00",
            "end_time": "10:00:00",
            "timezone": "Europe/Berlin",
            "by_weekday": ["monday"],
            "title": "DST Event",
            "location_mode": "online",
            "online_url": "https://example.org/live",
            "status": "draft",
        },
        format="json",
    )

    assert response.status_code == 200

    berlin = ZoneInfo("Europe/Berlin")
    local_starts = [
        parse_api_datetime(item["start_datetime"]).astimezone(berlin)
        for item in response.data["occurrences"]
    ]
    assert [start.hour for start in local_starts] == [9, 9, 9]
    assert [start.utcoffset() for start in local_starts] == [
        timedelta(hours=1),
        timedelta(hours=2),
        timedelta(hours=2),
    ]


@pytest.mark.django_db
def test_events_patch_marks_generated_occurrence_as_exception(
    api_client, user, staff_user, campaign, event_type
):
    """Editing a generated occurrence directly should mark it as an exception."""
    _authenticate_org_writer(api_client, staff_user, "ROLE_DCS_WRITER")
    create_response = api_client.post(
        "/api/v1/event-series/",
        {
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "Exception Series",
            "series_mode": "recurring",
            "recurrence_type": "weekly",
            "start_date": "2026-04-06",
            "occurrence_count": 2,
            "interval": 1,
            "start_time": "18:00:00",
            "end_time": "19:00:00",
            "timezone": "Europe/Berlin",
            "by_weekday": ["monday"],
            "title": "Generated Event",
            "location_mode": "online",
            "online_url": "https://example.org/live",
            "status": "draft",
        },
        format="json",
    )
    series = EventSeries.objects.get(id=create_response.data["id"])
    event = series.events.order_by("occurrence_index").first()
    original_start = event.start_datetime

    new_start = original_start + timedelta(hours=2)
    response = api_client.patch(
        f"/api/v1/events/{event.id}/",
        {
            "start_datetime": new_start.isoformat(),
            "end_datetime": (new_start + timedelta(hours=1)).isoformat(),
        },
        format="json",
    )

    assert response.status_code == 200
    event.refresh_from_db()
    assert event.is_exception is True
    assert event.original_start_datetime == original_start
    assert event.start_datetime == new_start


@pytest.mark.django_db
def test_event_series_patch_skips_future_exception_occurrences_by_default(
    api_client, user, staff_user, campaign, event_type
):
    """Future bulk updates should leave exception occurrences untouched by default."""
    today = timezone.localdate()
    days_until_monday = (0 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    start_date = today + timedelta(days=days_until_monday)

    _authenticate_org_writer(api_client, staff_user, "ROLE_DCS_WRITER")
    create_response = api_client.post(
        "/api/v1/event-series/",
        {
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "Bulk Update Series",
            "series_mode": "recurring",
            "recurrence_type": "weekly",
            "start_date": start_date.isoformat(),
            "occurrence_count": 3,
            "interval": 1,
            "start_time": "18:00:00",
            "end_time": "19:00:00",
            "timezone": "Europe/Berlin",
            "by_weekday": ["monday"],
            "title": "Original Series Title",
            "location_mode": "online",
            "online_url": "https://example.org/live",
            "status": "draft",
        },
        format="json",
    )
    series = EventSeries.objects.get(id=create_response.data["id"])
    events = list(series.events.order_by("occurrence_index"))

    exception_response = api_client.patch(
        f"/api/v1/events/{events[1].id}/",
        {"title": "Exception Title"},
        format="json",
    )
    assert exception_response.status_code == 200

    response = api_client.patch(
        f"/api/v1/event-series/{series.id}/",
        {"title": "Updated Series Title"},
        format="json",
    )

    assert response.status_code == 200
    series.refresh_from_db()
    events = list(series.events.order_by("occurrence_index"))
    assert events[0].title == "Updated Series Title"
    assert events[1].title == "Exception Title"
    assert events[1].is_exception is True
    assert events[2].title == "Updated Series Title"


@pytest.mark.django_db
def test_events_create(api_client, user, campaign):
    """Test creating a new event."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    data = {
        "title": "New Event",
        "summary": "Test summary",
        "campaign": str(campaign.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
        "location_mode": "online",
        "online_url": "https://example.org/live",
        "online_platform": "Zoom",
        "status": "draft",
    }
    response = api_client.post("/api/v1/events/", data, format="json")
    assert response.status_code == 201
    assert response.data["title"] == "New Event"
    assert response.data["organizer"] == user.id
    assert response.data["location_mode"] == "online"


@pytest.mark.django_db
def test_events_create_assigns_taxonomy_terms(api_client, user, campaign):
    """Event creation should persist taxonomy term assignments."""
    topic_dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    audience_dimension = TaxonomyDimension.objects.create(
        code="audience",
        label="Audience",
        selection_mode=TaxonomyDimension.SelectionMode.SINGLE,
    )
    climate = TaxonomyTerm.objects.create(
        dimension=topic_dimension,
        code="climate",
        label="Climate",
    )
    mobility = TaxonomyTerm.objects.create(
        dimension=topic_dimension,
        code="mobility",
        label="Mobility",
    )
    youth = TaxonomyTerm.objects.create(
        dimension=audience_dimension,
        code="youth",
        label="Youth",
    )

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    data = {
        "title": "Tagged Event",
        "campaign": str(campaign.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
        "location_mode": "online",
        "online_url": "https://example.org/live",
        "status": "draft",
        "taxonomy_assignments": [
            {
                "dimension_id": str(topic_dimension.id),
                "term_ids": [str(climate.id), str(mobility.id)],
            },
            {
                "dimension_id": str(audience_dimension.id),
                "term_ids": [str(youth.id)],
            },
        ],
    }
    response = api_client.post("/api/v1/events/", data, format="json")

    assert response.status_code == 201
    event = Event.objects.get(id=response.data["id"])
    assigned_term_ids = set(
        EventTerm.objects.filter(event=event).values_list("term_id", flat=True)
    )
    assert assigned_term_ids == {climate.id, mobility.id, youth.id}


@pytest.mark.django_db
def test_events_create_rejects_multiple_single_select_terms(api_client, user, campaign):
    """Serializer validation should reject conflicting single-select terms."""
    dimension = TaxonomyDimension.objects.create(
        code="audience",
        label="Audience",
        selection_mode=TaxonomyDimension.SelectionMode.SINGLE,
    )
    youth = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="youth",
        label="Youth",
    )
    seniors = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="seniors",
        label="Seniors",
    )

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    data = {
        "title": "Conflicting Tagged Event",
        "campaign": str(campaign.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
        "location_mode": "online",
        "online_url": "https://example.org/live",
        "status": "draft",
        "taxonomy_assignments": [
            {
                "dimension_id": str(dimension.id),
                "term_ids": [str(youth.id), str(seniors.id)],
            }
        ],
    }
    response = api_client.post("/api/v1/events/", data, format="json")

    assert response.status_code == 400
    assert "taxonomy_assignments" in response.data


@pytest.mark.django_db
def test_events_create_rejects_duplicate_taxonomy_dimensions(api_client, user, campaign):
    """Grouped taxonomy assignments should not repeat the same dimension."""
    dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    climate = TaxonomyTerm.objects.create(dimension=dimension, code="climate", label="Climate")
    mobility = TaxonomyTerm.objects.create(dimension=dimension, code="mobility", label="Mobility")

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    data = {
        "title": "Duplicate Dimension Event",
        "campaign": str(campaign.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
        "location_mode": "online",
        "online_url": "https://example.org/live",
        "status": "published",
        "taxonomy_assignments": [
            {"dimension_id": str(dimension.id), "term_ids": [str(climate.id)]},
            {"dimension_id": str(dimension.id), "term_ids": [str(mobility.id)]},
        ],
    }

    response = api_client.post("/api/v1/events/", data, format="json")

    assert response.status_code == 400
    assert "taxonomy_assignments" in response.data


@pytest.mark.django_db
def test_events_create_rejects_non_leaf_taxonomy_term(api_client, user, campaign):
    """Only leaf taxonomy terms should be assignable."""
    dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    parent = TaxonomyTerm.objects.create(dimension=dimension, code="health", label="Health")
    TaxonomyTerm.objects.create(
        dimension=dimension,
        parent=parent,
        code="mental-health",
        label="Mental Health",
    )

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    data = {
        "title": "Parent Tagged Event",
        "campaign": str(campaign.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
        "location_mode": "online",
        "online_url": "https://example.org/live",
        "status": "published",
        "taxonomy_assignments": [
            {"dimension_id": str(dimension.id), "term_ids": [str(parent.id)]}
        ],
    }

    response = api_client.post("/api/v1/events/", data, format="json")

    assert response.status_code == 400
    assert "taxonomy_assignments" in response.data


@pytest.mark.django_db
def test_events_create_rejects_inactive_taxonomy_term(api_client, user, campaign):
    """Inactive taxonomy terms should not be assignable in new writes."""
    dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    inactive_term = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="inactive-topic",
        label="Inactive Topic",
        is_active=False,
    )

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    data = {
        "title": "Inactive Tagged Event",
        "campaign": str(campaign.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
        "location_mode": "online",
        "online_url": "https://example.org/live",
        "status": "published",
        "taxonomy_assignments": [
            {"dimension_id": str(dimension.id), "term_ids": [str(inactive_term.id)]}
        ],
    }

    response = api_client.post("/api/v1/events/", data, format="json")

    assert response.status_code == 400
    assert "taxonomy_assignments" in response.data


@pytest.mark.django_db
def test_event_series_create_assigns_grouped_taxonomy_to_generated_occurrences(
    api_client, user, campaign, event_type
):
    """Series creation should persist grouped taxonomy assignments onto occurrences."""
    topic_dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    audience_dimension = TaxonomyDimension.objects.create(
        code="audience",
        label="Audience",
        selection_mode=TaxonomyDimension.SelectionMode.SINGLE,
    )
    climate = TaxonomyTerm.objects.create(
        dimension=topic_dimension,
        code="climate",
        label="Climate",
    )
    youth = TaxonomyTerm.objects.create(
        dimension=audience_dimension,
        code="youth",
        label="Youth",
    )

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.post(
        "/api/v1/event-series/",
        {
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "Tagged Series",
            "series_mode": "recurring",
            "recurrence_type": "weekly",
            "start_date": "2026-04-06",
            "occurrence_count": 2,
            "interval": 1,
            "start_time": "18:00:00",
            "end_time": "19:30:00",
            "timezone": "Europe/Berlin",
            "by_weekday": ["monday"],
            "title": "Recurring Tagged Event",
            "location_mode": "online",
            "online_url": "https://example.org/live",
            "status": "draft",
            "taxonomy_assignments": [
                {"dimension_id": str(topic_dimension.id), "term_ids": [str(climate.id)]},
                {"dimension_id": str(audience_dimension.id), "term_ids": [str(youth.id)]},
            ],
        },
        format="json",
    )

    assert response.status_code == 201
    series = EventSeries.objects.get(id=response.data["id"])
    for event in series.events.all():
        assigned_term_ids = set(
            EventTerm.objects.filter(event=event).values_list("term_id", flat=True)
        )
        assert assigned_term_ids == {climate.id, youth.id}


@pytest.mark.django_db
def test_event_series_create_rejects_invalid_grouped_taxonomy_assignments(
    api_client, user, campaign, event_type
):
    """Series creation should reject invalid grouped taxonomy combinations."""
    dimension = TaxonomyDimension.objects.create(
        code="audience",
        label="Audience",
        selection_mode=TaxonomyDimension.SelectionMode.SINGLE,
    )
    youth = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="youth",
        label="Youth",
    )
    seniors = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="seniors",
        label="Seniors",
    )

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.post(
        "/api/v1/event-series/",
        {
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "Invalid Tagged Series",
            "series_mode": "recurring",
            "recurrence_type": "weekly",
            "start_date": "2026-04-06",
            "occurrence_count": 2,
            "interval": 1,
            "start_time": "18:00:00",
            "end_time": "19:30:00",
            "timezone": "Europe/Berlin",
            "by_weekday": ["monday"],
            "title": "Recurring Invalid Tagged Event",
            "location_mode": "online",
            "online_url": "https://example.org/live",
            "status": "draft",
            "taxonomy_assignments": [
                {
                    "dimension_id": str(dimension.id),
                    "term_ids": [str(youth.id), str(seniors.id)],
                }
            ],
        },
        format="json",
    )

    assert response.status_code == 400
    assert "taxonomy_assignments" in response.data


@pytest.mark.django_db
def test_events_create_rejects_online_event_without_access_data(api_client, user, campaign):
    """Online events without access data should be rejected."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    data = {
        "title": "Broken Online Event",
        "campaign": str(campaign.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
        "location_mode": "online",
        "status": "draft",
    }
    response = api_client.post("/api/v1/events/", data, format="json")
    assert response.status_code == 400
    assert "online_url" in response.data


@pytest.mark.django_db
def test_events_list_filters_by_term(api_client, user, campaign):
    """Filtering by term_id should return only tagged matches."""
    dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    climate = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="climate",
        label="Climate",
    )
    mobility = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="mobility",
        label="Mobility",
    )
    tagged_event = Event.objects.create(
        campaign=campaign,
        title="Climate Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    other_event = Event.objects.create(
        campaign=campaign,
        title="Mobility Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(10.1, 53.6, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    EventTerm.objects.create(event=tagged_event, term=climate)
    EventTerm.objects.create(event=other_event, term=mobility)

    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/?term_id={climate.id}")

    assert response.status_code == 200
    titles = [event["title"] for event in response.data["results"]]
    assert titles == ["Climate Event"]


@pytest.mark.django_db
def test_events_list_filters_by_dimension_and_term(api_client, user, campaign):
    """Combined dimension_id and term_id filters should return the expected event set."""
    topic_dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    audience_dimension = TaxonomyDimension.objects.create(code="audience", label="Audience")
    climate = TaxonomyTerm.objects.create(
        dimension=topic_dimension,
        code="climate",
        label="Climate",
    )
    youth = TaxonomyTerm.objects.create(
        dimension=audience_dimension,
        code="youth",
        label="Youth",
    )
    climate_event = Event.objects.create(
        campaign=campaign,
        title="Climate Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    mixed_event = Event.objects.create(
        campaign=campaign,
        title="Climate Youth Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(10.1, 53.6, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    EventTerm.objects.create(event=climate_event, term=climate)
    EventTerm.objects.create(event=mixed_event, term=climate)
    EventTerm.objects.create(event=mixed_event, term=youth)

    api_client.force_authenticate(user=user)
    response = api_client.get(
        f"/api/v1/events/?dimension_id={audience_dimension.id}&term_id={climate.id}"
    )

    assert response.status_code == 200
    titles = [event["title"] for event in response.data["results"]]
    assert titles == ["Climate Youth Event"]


@pytest.mark.django_db
def test_events_delete(api_client, user, future_event):
    """Test deleting an event."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_ADMIN")
    response = api_client.delete(f"/api/v1/events/{future_event.id}/")
    assert response.status_code == 204
    assert not Event.objects.filter(id=future_event.id).exists()


@pytest.mark.django_db
def test_event_detail_returns_layer_summary(api_client, user, future_event):
    """Event detail must return canonical Layer metadata for linked layers."""
    from tosca_api.apps.events.models import EventLayer
    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    layer = make_layer("workspace:event_detail_layer", user=user)
    EventLayer.objects.create(event=future_event, layer=layer, display_order=1)

    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/{future_event.id}/")
    assert response.status_code == 200

    layers = response.data["layers"]
    assert len(layers) == 1
    layer_payload = layers[0]["layer"]
    assert layer_payload["name"] == "event_detail_layer"
    assert layer_payload["workspace"]["name"] == "workspace"
    assert layer_payload["geometry_type"] == "Point"
    assert layer_payload["srid"] == 4326
    assert layer_payload["is_public"] is True
    assert layer_payload["publishing_state"] == "PUBLISHED"
    assert layers[0]["display_order"] == 1


@pytest.mark.django_db
def test_event_create_with_layer_uuids(api_client, user, campaign):
    """POST with layers=[uuid] must persist EventLayer rows in order."""
    from tosca_api.apps.events.models import EventLayer
    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    l1 = make_layer("workspace:evt_w_1", user=user)
    l2 = make_layer("workspace:evt_w_2", user=user)

    now = timezone.now()
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    payload = {
        "title": "Event w/ layers",
        "campaign": str(campaign.id),
        "start_datetime": now.isoformat(),
        "end_datetime": (now + timedelta(hours=1)).isoformat(),
        "location": {"type": "Point", "coordinates": [10.0, 53.5]},
        "location_mode": "physical",
        "layers": [str(l1.id), str(l2.id)],
    }
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 201, response.data

    event = Event.objects.get(id=response.data["id"])
    rows = list(EventLayer.objects.filter(event=event).order_by("display_order"))
    assert [r.layer_id for r in rows] == [l1.id, l2.id]


@pytest.mark.django_db
def test_event_create_rejects_unpublished_layer(api_client, user, campaign):
    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    draft_layer = make_layer(
        "workspace:evt_w_draft", user=user, publishing_state="DRAFT"
    )
    now = timezone.now()
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    payload = {
        "title": "Bad layers",
        "campaign": str(campaign.id),
        "start_datetime": now.isoformat(),
        "end_datetime": (now + timedelta(hours=1)).isoformat(),
        "location": {"type": "Point", "coordinates": [10.0, 53.5]},
        "location_mode": "physical",
        "layers": [str(draft_layer.id)],
    }
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 400
    assert "layers" in response.data
