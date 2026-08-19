"""Two-org (DCS + HPA) DJANGO_STAFF acceptance matrix (security tickets
2026-08-19 ticket 07).

Proves the intended hierarchy end to end, across both the admin path
(queryset scope + has_*_permission) and the DRF path (permission classes +
queryset), so the 2026-08-19 incident -- a DJANGO_STAFF+ROLE_HPA_WRITER user
editing a DCS Workspace's description -- and the class of regression it
represents cannot silently return.

Per-resource contract (must agree with ticket 02's visibility policy):
- Public-read (GeoStory, Event): cross-org published/public read -> 200;
  cross-org draft/unpublished read -> 404.
- Org-private (Campaign, Workspace description/admin): all cross-org access
  denied (read via admin queryset scope; write via has_*_permission/DRF
  permission classes).
- Any write/change/delete/admin-config: cross-org denied for everyone except
  DJANGO_SUPERADMIN.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.test import Client, RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.campaigns.admin import CampaignAdmin
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import Event
from tosca_api.apps.geodata_providers.admin import WorkspaceAdmin
from tosca_api.apps.geodata_providers.models import GeodataEngine, Workspace
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement
from tosca_api.apps.organizations.policy import sync_snapshot

User = get_user_model()


@pytest.fixture
def factory():
    return RequestFactory()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def dcs_org(db):
    # Pre-seeded (migration 0002) and entitled to every TOSCA_ENTITLEABLE_APPS
    # app (migration 0005).
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def hpa_org(db):
    org, _ = Organization.objects.get_or_create(slug="hpa", defaults={"name": "HPA"})
    for app_label in ("campaigns", "geostories", "events", "geodata_providers"):
        OrganizationAppEntitlement.objects.get_or_create(organization=org, app_label=app_label)
    return org


@pytest.fixture
def engine(db):
    creator = User.objects.create_user(username="matrix-engine-owner")
    return GeodataEngine.objects.create(
        name="matrix-engine",
        engine_type="geoserver",
        base_url="http://example.com/geoserver",
        public_url="http://example.com/geoserver",
        admin_username="admin",
        admin_password="secret",
        created_by=creator,
    )


@pytest.fixture
def dcs_workspace(dcs_org, engine):
    creator = User.objects.create_user(username="dcs-ws-owner-matrix")
    return Workspace.objects.create(
        organization=dcs_org, geodata_engine=engine, name="dcs_matrix_ws",
        description="original DCS description", created_by=creator,
    )


@pytest.fixture
def dcs_campaign(dcs_org):
    creator = User.objects.create_user(username="dcs-camp-owner-matrix")
    return Campaign.objects.create(title="DCS Matrix Campaign", organization=dcs_org, created_by=creator)


@pytest.fixture
def hpa_campaign(hpa_org):
    creator = User.objects.create_user(username="hpa-camp-owner-matrix")
    return Campaign.objects.create(title="HPA Matrix Campaign", organization=hpa_org, created_by=creator)


@pytest.fixture
def dcs_draft_story(dcs_campaign):
    author = User.objects.create_user(username="dcs-story-author-matrix")
    return GeoStory.objects.create(
        title="DCS Draft Story", campaign=dcs_campaign, author=author, status=GeoStory.Status.DRAFT,
    )


@pytest.fixture
def dcs_published_story(dcs_campaign):
    author = User.objects.create_user(username="dcs-pub-story-author-matrix")
    return GeoStory.objects.create(
        title="DCS Published Story", campaign=dcs_campaign, author=author,
        status=GeoStory.Status.PUBLISHED,
    )


@pytest.fixture
def dcs_draft_event(dcs_campaign):
    organizer = User.objects.create_user(username="dcs-event-organizer-matrix")
    return Event.objects.create(
        campaign=dcs_campaign,
        title="DCS Draft Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=organizer,
        status=Event.Status.DRAFT,
        visibility=Event.Visibility.PUBLIC,
    )


@pytest.fixture
def dcs_published_event(dcs_campaign):
    organizer = User.objects.create_user(username="dcs-pub-event-organizer-matrix")
    return Event.objects.create(
        campaign=dcs_campaign,
        title="DCS Published Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=organizer,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
    )


def _token(*roles, org_slug="hpa"):
    return {"realm_access": {"roles": list(roles)}, "default_organization": org_slug}


def _staff_user(username, *, org=None, level=None, superuser=False):
    user = User.objects.create_user(username=username, is_staff=True, is_superuser=superuser)
    if org and level:
        user._auth_claims = AuthClaims(org_roles={org: level}, default_org=org, authoritative=True)
    return user


def _admin_request(factory, user):
    request = factory.get("/admin/")
    request.user = user
    return request


def _authenticate_api(api_client, user, *roles, org="hpa"):
    api_client.force_authenticate(user=user, token=_token(*roles, org_slug=org))


def _staff_session_user(username, *, org=None, level=None, superuser=False):
    """A DJANGO_STAFF user for real ``django.test.Client`` session-auth
    requests. Unlike ``_staff_user``'s ``user._auth_claims`` (only visible to
    the in-memory object held by the caller), a real ``Client`` request
    re-loads the user from the DB per request, so claims must be persisted
    to ``UserAuthorizationSnapshot`` (the ticket-05 browser-path fallback)
    to be seen at all."""
    user = User.objects.create_user(username=username, is_staff=True, is_superuser=superuser, password="pw")
    if org and level:
        sync_snapshot(
            user,
            AuthClaims(org_roles={org: level}, default_org=org, authoritative=True, platform_exempt=False),
        )
    return user


# StoreInline's management-form fields are required by WorkspaceAdmin's
# changeform even with zero stores.
_WORKSPACE_INLINE_MANAGEMENT_FORM = {
    "stores-TOTAL_FORMS": "0",
    "stores-INITIAL_FORMS": "0",
    "stores-MIN_NUM_FORMS": "0",
    "stores-MAX_NUM_FORMS": "1000",
}


# ---------------------------------------------------------------------------
# The incident, directly: DJANGO_STAFF+ROLE_HPA_WRITER cannot see/edit a DCS
# Workspace through the admin.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_incident_hpa_staff_cannot_reach_dcs_workspace_via_admin_queryset(
    factory, dcs_org, hpa_org, dcs_workspace,
):
    hpa_staff = _staff_user("hpa-writer-incident", org="hpa", level="WRITER")
    request = _admin_request(factory, hpa_staff)
    model_admin = WorkspaceAdmin(Workspace, admin_site=None)

    qs = model_admin.get_queryset(request)

    assert not qs.filter(pk=dcs_workspace.pk).exists()


@pytest.mark.django_db
def test_dcs_staff_can_reach_own_workspace_via_admin_queryset(factory, dcs_org, dcs_workspace):
    dcs_staff = _staff_user("dcs-writer-incident", org="dcs", level="WRITER")
    request = _admin_request(factory, dcs_staff)
    model_admin = WorkspaceAdmin(Workspace, admin_site=None)

    qs = model_admin.get_queryset(request)

    assert qs.filter(pk=dcs_workspace.pk).exists()


@pytest.mark.django_db
def test_superadmin_reaches_both_orgs_workspaces_via_admin_queryset(factory, dcs_org, dcs_workspace):
    superadmin = _staff_user("superadmin-incident", superuser=True)
    request = _admin_request(factory, superadmin)
    model_admin = WorkspaceAdmin(Workspace, admin_site=None)

    qs = model_admin.get_queryset(request)

    assert qs.filter(pk=dcs_workspace.pk).exists()


# ---------------------------------------------------------------------------
# Org-private resource, admin path: Campaign (queryset scope)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_campaign_admin_hpa_reader_sees_only_hpa(factory, dcs_org, hpa_org, dcs_campaign, hpa_campaign):
    hpa_reader = _staff_user("hpa-reader-campaign", org="hpa", level="READER")
    request = _admin_request(factory, hpa_reader)
    model_admin = CampaignAdmin(Campaign, admin_site=None)

    qs = model_admin.get_queryset(request)

    assert qs.filter(pk=hpa_campaign.pk).exists()
    assert not qs.filter(pk=dcs_campaign.pk).exists()


@pytest.mark.django_db
def test_campaign_admin_superadmin_sees_both_orgs(factory, dcs_org, hpa_org, dcs_campaign, hpa_campaign):
    superadmin = _staff_user("superadmin-campaign", superuser=True)
    request = _admin_request(factory, superadmin)
    model_admin = CampaignAdmin(Campaign, admin_site=None)

    qs = model_admin.get_queryset(request)

    assert qs.filter(pk=hpa_campaign.pk).exists()
    assert qs.filter(pk=dcs_campaign.pk).exists()


# ---------------------------------------------------------------------------
# Public-read resources, DRF path: GeoStory + Event
# published/public -> 200 cross-org; draft/unpublished -> 404 cross-org;
# own-org draft -> 200.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_geostory_hpa_reader_sees_dcs_published_but_not_dcs_draft(
    api_client, dcs_org, hpa_org, dcs_draft_story, dcs_published_story,
):
    hpa_reader = _staff_user("hpa-reader-story", org="hpa", level="READER")
    _authenticate_api(api_client, hpa_reader, "ROLE_HPA_READER", org="hpa")

    published_response = api_client.get(f"/api/v1/stories/{dcs_published_story.id}/")
    draft_response = api_client.get(f"/api/v1/stories/{dcs_draft_story.id}/")

    assert published_response.status_code == 200
    assert draft_response.status_code == 404


@pytest.mark.django_db
def test_geostory_dcs_reader_sees_own_org_draft(api_client, dcs_org, dcs_draft_story):
    dcs_reader = _staff_user("dcs-reader-story", org="dcs", level="READER")
    _authenticate_api(api_client, dcs_reader, "ROLE_DCS_READER", org="dcs")

    response = api_client.get(f"/api/v1/stories/{dcs_draft_story.id}/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_geostory_anon_sees_published_not_draft(api_client, dcs_draft_story, dcs_published_story):
    published_response = api_client.get(f"/api/v1/stories/{dcs_published_story.id}/")
    draft_response = api_client.get(f"/api/v1/stories/{dcs_draft_story.id}/")

    assert published_response.status_code == 200
    assert draft_response.status_code == 404


@pytest.mark.django_db
def test_event_hpa_staff_sees_dcs_published_but_not_dcs_draft(
    api_client, dcs_org, hpa_org, dcs_draft_event, dcs_published_event,
):
    hpa_staff = _staff_user("hpa-staff-event", org="hpa", level="READER")
    _authenticate_api(api_client, hpa_staff, "ROLE_HPA_READER", org="hpa")

    published_response = api_client.get(f"/api/v1/events/{dcs_published_event.id}/")
    draft_response = api_client.get(f"/api/v1/events/{dcs_draft_event.id}/")

    assert published_response.status_code == 200
    assert draft_response.status_code == 404


@pytest.mark.django_db
def test_event_dcs_staff_sees_own_org_draft(api_client, dcs_org, dcs_draft_event):
    dcs_staff = _staff_user("dcs-staff-event", org="dcs", level="READER")
    _authenticate_api(api_client, dcs_staff, "ROLE_DCS_READER", org="dcs")

    response = api_client.get(f"/api/v1/events/{dcs_draft_event.id}/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_event_bare_django_staff_no_org_role_cannot_see_dcs_draft(api_client, dcs_org, dcs_draft_event):
    """Regression guard for the incident's root cause: DJANGO_STAFF alone
    (no org role) is no longer platform-exempt."""
    bare_staff = _staff_user("bare-staff-event")
    api_client.force_authenticate(user=bare_staff)

    response = api_client.get(f"/api/v1/events/{dcs_draft_event.id}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_event_anon_sees_published_not_draft(api_client, dcs_draft_event, dcs_published_event):
    published_response = api_client.get(f"/api/v1/events/{dcs_published_event.id}/")
    draft_response = api_client.get(f"/api/v1/events/{dcs_draft_event.id}/")

    assert published_response.status_code == 200
    assert draft_response.status_code == 404


# ---------------------------------------------------------------------------
# DJANGO_SUPERADMIN: full cross-org access via the DRF path too.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_superadmin_reads_dcs_draft_story_and_event(
    api_client, dcs_org, dcs_draft_story, dcs_draft_event,
):
    superadmin = _staff_user("superadmin-drf-story-event", superuser=True)
    api_client.force_authenticate(user=superadmin, token={"realm_access": {"roles": ["DJANGO_SUPERADMIN"]}})

    story_response = api_client.get(f"/api/v1/stories/{dcs_draft_story.id}/")
    event_response = api_client.get(f"/api/v1/events/{dcs_draft_event.id}/")

    assert story_response.status_code == 200
    assert event_response.status_code == 200


# ---------------------------------------------------------------------------
# The incident, end to end: a real ``django.test.Client`` hitting the real
# admin URL (session auth, CSRF, full changeform_view/get_object dispatch --
# not just the isolated ``get_queryset()`` call above). This is the
# regression guard for "a constant gains a role, tenant isolation silently
# breaks everywhere" -- ORG_CHECK_EXEMPT_ROLES must never again include
# DJANGO_STAFF.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_incident_http_hpa_writer_denied_dcs_workspace_change(dcs_org, hpa_org, dcs_workspace):
    """HPA WRITER hits the real admin URL for a DCS Workspace.

    ``WorkspaceAdmin.get_queryset`` (``OrgScopedAdminMixin``) filters the DCS
    row out for an HPA-scoped caller entirely, so Django's own
    ``_changeform_view`` never finds an object to check -- it takes the
    stock "object doesn't exist" branch (``_get_obj_does_not_exist_redirect``:
    a warning message + 302 to the admin index), the same idiom Django uses
    for a bad/deleted id. This *is* the 404-equivalent the canonical doc
    calls for (epic-11-canonical.md §10a: "Cross-org erişim → 404, 403
    değil") -- just expressed as Django admin's own redirect-on-missing-
    object convention rather than a literal HTTP 404/403 status. What
    matters for the incident is verified directly: no object is ever
    fetched, so ``save_model`` never runs and the description is untouched.
    """
    hpa_writer = _staff_session_user("hpa-writer-http-incident", org="hpa", level="WRITER")
    client = Client()
    client.force_login(hpa_writer)
    url = f"/admin/geodata_providers/workspace/{dcs_workspace.pk}/change/"

    get_response = client.get(url)
    post_response = client.post(url, {
        "name": dcs_workspace.name,
        "description": "HACKED BY HPA WRITER",
        "organization": dcs_org.pk,
        "geodata_engine": dcs_workspace.geodata_engine_id,
        **_WORKSPACE_INLINE_MANAGEMENT_FORM,
    })
    dcs_workspace.refresh_from_db()

    assert get_response.status_code == 302
    assert get_response.url == "/admin/"
    assert post_response.status_code == 302
    assert post_response.url == "/admin/"
    assert dcs_workspace.description == "original DCS description"


@pytest.mark.django_db
def test_incident_http_dcs_writer_can_change_own_org_workspace(dcs_org, dcs_workspace):
    dcs_writer = _staff_session_user("dcs-writer-http-incident", org="dcs", level="WRITER")
    client = Client()
    client.force_login(dcs_writer)
    url = f"/admin/geodata_providers/workspace/{dcs_workspace.pk}/change/"

    post_response = client.post(url, {
        "name": dcs_workspace.name,
        "description": "EDITED BY DCS WRITER",
        "organization": dcs_org.pk,
        "geodata_engine": dcs_workspace.geodata_engine_id,
        **_WORKSPACE_INLINE_MANAGEMENT_FORM,
    })
    dcs_workspace.refresh_from_db()

    assert post_response.status_code == 302
    assert dcs_workspace.description == "EDITED BY DCS WRITER"


@pytest.mark.django_db
def test_incident_http_superadmin_can_change_cross_org_workspace(dcs_org, dcs_workspace):
    superadmin = _staff_session_user("superadmin-http-incident", superuser=True)
    client = Client()
    client.force_login(superadmin)
    url = f"/admin/geodata_providers/workspace/{dcs_workspace.pk}/change/"

    post_response = client.post(url, {
        "name": dcs_workspace.name,
        "description": "EDITED BY SUPERADMIN",
        "organization": dcs_org.pk,
        "geodata_engine": dcs_workspace.geodata_engine_id,
        **_WORKSPACE_INLINE_MANAGEMENT_FORM,
    })
    dcs_workspace.refresh_from_db()

    assert post_response.status_code == 302
    assert dcs_workspace.description == "EDITED BY SUPERADMIN"
