from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import (
    Event,
    EventType,
    PublicHealthEventProfile,
)

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


def _org_token(*roles, org="dcs"):
    """Keycloak-shaped token for org-scoped writes (epic-11 PR1 SS3.3)."""
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
    from tosca_api.apps.authentication.role_sync import AuthClaims

    level = roles[0].rsplit("_", 1)[-1] if roles else None
    if level:
        user._auth_claims = AuthClaims(
            org_roles={org: level}, default_org=org, authoritative=True
        )
    api_client.force_authenticate(user=user, token=_org_token(*roles, org=org))


@pytest.fixture
def user():
    return User.objects.create_user(username="phase8", password="pw")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Phase 8", created_by=user)


@pytest.fixture
def general_event_type():
    return EventType.objects.create(
        code="phase8-general",
        label="General",
        profile_mode=EventType.ProfileMode.CORE,
    )


@pytest.fixture
def ph_event_type():
    return EventType.objects.create(
        code="phase8-ph",
        label="Public Health",
        profile_mode=EventType.ProfileMode.EXTENSION,
        profile_key="public_health",
    )


def _payload(campaign, event_type, **overrides):
    base = {
        "title": "PH event",
        "summary": "PH summary",
        "campaign": str(campaign.id),
        "event_type": str(event_type.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=1)).isoformat(),
        "location_mode": "physical",
        "location": {"type": "Point", "coordinates": [10.0, 53.5]},
        "status": "published",
        "provider_phone": "+49 89 12345",
    }
    base.update(overrides)
    return base


@pytest.mark.django_db
def test_general_event_detail_returns_null_profile(
    api_client, user, campaign, general_event_type
):
    event = Event.objects.create(
        campaign=campaign,
        event_type=general_event_type,
        title="General",
        summary="General",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        provider_phone="+49 89 12345",
    )
    response = api_client.get(f"/api/v1/events/{event.id}/")
    assert response.status_code == 200
    assert response.data["profile_key"] == ""
    assert response.data["profile"] is None


@pytest.mark.django_db
def test_ph_event_without_profile_row_returns_null_profile(
    api_client, user, campaign, ph_event_type
):
    event = Event.objects.create(
        campaign=campaign,
        event_type=ph_event_type,
        title="PH no profile",
        summary="PH",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        provider_phone="+49 89 12345",
    )
    response = api_client.get(f"/api/v1/events/{event.id}/")
    assert response.status_code == 200
    assert response.data["profile_key"] == "public_health"
    assert response.data["profile"] is None


@pytest.mark.django_db
def test_ph_event_profile_round_trips_via_api(
    api_client, user, campaign, ph_event_type
):
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    payload = _payload(
        campaign,
        ph_event_type,
        profile={
            "target_age_note": "35-50",
            "registration": "required",
            "short_notice_possible": True,
            "cost_amount_eur": "40.00",
            "reduced_amount_eur": "20.00",
            "subsidy_program": "Krankenkasse XYZ",
            "transit_note": "U-Bahn 3 min",
            "insurance_eligible": True,
            "referral_required": False,
        },
    )
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 201, response.data
    event_id = response.data["id"]

    detail = api_client.get(f"/api/v1/events/{event_id}/")
    assert detail.status_code == 200
    assert detail.data["profile_key"] == "public_health"
    profile = detail.data["profile"]
    assert profile["target_age_note"] == "35-50"
    assert profile["registration"] == "required"
    assert profile["short_notice_possible"] is True
    assert profile["cost_amount_eur"] == "40.00"
    assert profile["reduced_amount_eur"] == "20.00"
    assert profile["subsidy_program"] == "Krankenkasse XYZ"
    assert profile["transit_note"] == "U-Bahn 3 min"
    assert profile["insurance_eligible"] is True


@pytest.mark.django_db
def test_general_event_rejects_profile_payload(
    api_client, user, campaign, general_event_type
):
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    payload = _payload(
        campaign,
        general_event_type,
        profile={"registration": "required"},
    )
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 400
    assert "profile" in response.data


@pytest.mark.django_db
def test_profile_rejects_reduced_greater_than_cost(
    api_client, user, campaign, ph_event_type
):
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    payload = _payload(
        campaign,
        ph_event_type,
        profile={
            "cost_amount_eur": "10.00",
            "reduced_amount_eur": "20.00",
        },
    )
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 400
    assert "reduced_amount_eur" in response.data["profile"]


@pytest.mark.django_db
def test_profile_rejects_negative_cost(api_client, user, campaign, ph_event_type):
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    payload = _payload(
        campaign,
        ph_event_type,
        profile={"cost_amount_eur": "-5.00"},
    )
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 400
    assert "cost_amount_eur" in response.data["profile"]


@pytest.mark.django_db
def test_profile_patch_upserts_existing_row(api_client, user, campaign, ph_event_type):
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    create_response = api_client.post(
        "/api/v1/events/",
        _payload(
            campaign,
            ph_event_type,
            profile={"registration": "required", "cost_amount_eur": "10.00"},
        ),
        format="json",
    )
    assert create_response.status_code == 201
    event_id = create_response.data["id"]

    patch_response = api_client.patch(
        f"/api/v1/events/{event_id}/",
        {"profile": {"registration": "not_required", "cost_amount_eur": None}},
        format="json",
    )
    assert patch_response.status_code == 200, patch_response.data

    profile = PublicHealthEventProfile.objects.get(event_id=event_id)
    assert profile.registration == "not_required"
    assert profile.cost_amount_eur is None


@pytest.mark.django_db
def test_profile_model_clean_rejects_reduced_greater_than_cost(
    user, campaign, ph_event_type
):
    event = Event.objects.create(
        campaign=campaign,
        event_type=ph_event_type,
        title="Cost test",
        summary="Cost",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        provider_phone="+49 89 12345",
    )
    profile = PublicHealthEventProfile(
        event=event,
        cost_amount_eur=Decimal("10.00"),
        reduced_amount_eur=Decimal("15.00"),
    )
    with pytest.raises(ValidationError) as exc:
        profile.clean()
    assert "reduced_amount_eur" in exc.value.message_dict
