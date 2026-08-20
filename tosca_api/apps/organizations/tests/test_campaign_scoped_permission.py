"""Unit tests for CampaignScopedPermission / validate_campaign_organization
(epic-11 PR1 §3.3, updated by security tickets ticket 09): write-gate for
Campaign-owned resources (Event, EventSeries, GeoStory, MediaAsset) that
derives org membership through `obj.campaign` rather than a direct
`obj.organization` FK.

As of ticket 09, this class is gate C only (org membership + object scope)
-- the action->level ladder (WRITER+ for writes, ADMIN for DELETE) moved to
`has_perm()` via `DjangoModelPermissionsOrAnonReadOnly` for role-controlled
models (Event, GeoStory); see `events/tests/test_permission_matrix.py` and
`geostories/tests/test_permission_matrix.py`.
"""

import pytest
from django.test import RequestFactory

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.organizations.models import Organization
from tosca_api.apps.organizations.permissions import (
    CampaignScopedPermission,
    validate_campaign_organization,
)


def _token(*roles, default_organization=None):
    payload = {"realm_access": {"roles": list(roles)}}
    if default_organization is not None:
        payload["default_organization"] = default_organization
    return payload


def _request(user, auth=None, method="GET"):
    factory = RequestFactory()
    request = getattr(factory, method.lower())("/fake/")
    request.user = user
    request.auth = auth
    return request


@pytest.fixture
def orgs(db):
    dcs, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    gq, _ = Organization.objects.get_or_create(slug="gq", defaults={"name": "GQ"})
    return dcs, gq


@pytest.fixture
def dcs_campaign(orgs, django_user_model):
    dcs, _ = orgs
    creator = django_user_model.objects.create_user(username="camp-creator")
    return Campaign.objects.create(title="DCS Campaign", created_by=creator, organization=dcs)


# ---------------------------------------------------------------------------
# has_permission (no object -- e.g. create)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_safe_methods_always_pass_without_role(django_user_model):
    user = django_user_model.objects.create_user(username="anon-ish")
    permission = CampaignScopedPermission()
    request = _request(user, auth=_token(), method="GET")

    assert permission.has_permission(request, None) is True


@pytest.mark.django_db
def test_write_requires_some_role_in_some_org(django_user_model):
    user = django_user_model.objects.create_user(username="no-role-writer")
    permission = CampaignScopedPermission()

    no_role_request = _request(user, auth=_token(default_organization="dcs"), method="POST")
    reader_request = _request(
        user, auth=_token("ROLE_DCS_READER", default_organization="dcs"), method="POST"
    )

    assert permission.has_permission(no_role_request, None) is False
    assert permission.has_permission(reader_request, None) is True


@pytest.mark.django_db
def test_delete_no_longer_requires_admin(django_user_model):
    """As of ticket 09, this class no longer gates capability -- any role
    passes DELETE too (WRITER+/ADMIN capability, where applicable, is
    ``has_perm()``'s job via ``DjangoModelPermissionsOrAnonReadOnly``)."""
    user = django_user_model.objects.create_user(username="writer-delete")
    permission = CampaignScopedPermission()

    reader_request = _request(
        user, auth=_token("ROLE_DCS_READER", default_organization="dcs"), method="DELETE"
    )

    assert permission.has_permission(reader_request, None) is True


# ---------------------------------------------------------------------------
# has_object_permission -- cross-org write rejection via obj.campaign
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_object_permission_denies_write_to_other_org_campaign(dcs_campaign, django_user_model):
    """A GQ writer must not be able to write an Event/GeoStory whose
    `.campaign.organization` is DCS -- derived through the campaign FK, not
    a nonexistent `obj.organization`."""
    user = django_user_model.objects.create_user(username="gq-writer")
    permission = CampaignScopedPermission()
    request = _request(
        user, auth=_token("ROLE_GQ_WRITER", default_organization="gq"), method="PATCH"
    )

    class FakeCampaignOwned:
        campaign = dcs_campaign

    assert permission.has_object_permission(request, None, FakeCampaignOwned()) is False


@pytest.mark.django_db
def test_object_permission_allows_write_to_own_org_campaign(dcs_campaign, django_user_model):
    user = django_user_model.objects.create_user(username="dcs-writer")
    permission = CampaignScopedPermission()
    request = _request(
        user, auth=_token("ROLE_DCS_WRITER", default_organization="dcs"), method="PATCH"
    )

    class FakeCampaignOwned:
        campaign = dcs_campaign

    assert permission.has_object_permission(request, None, FakeCampaignOwned()) is True


@pytest.mark.django_db
def test_object_permission_safe_method_passes_regardless_of_org(
    dcs_campaign, django_user_model
):
    user = django_user_model.objects.create_user(username="gq-reader")
    permission = CampaignScopedPermission()
    request = _request(
        user, auth=_token("ROLE_GQ_READER", default_organization="gq"), method="GET"
    )

    class FakeCampaignOwned:
        campaign = dcs_campaign

    assert permission.has_object_permission(request, None, FakeCampaignOwned()) is True


@pytest.mark.django_db
def test_exempt_role_bypasses_campaign_org_check(dcs_campaign, django_user_model):
    user = django_user_model.objects.create_user(username="superadmin-cs")
    permission = CampaignScopedPermission()
    request = _request(user, auth=_token("DJANGO_SUPERADMIN"), method="DELETE")

    class FakeCampaignOwned:
        campaign = dcs_campaign

    assert permission.has_object_permission(request, None, FakeCampaignOwned()) is True


# ---------------------------------------------------------------------------
# validate_campaign_organization -- create-time serializer-level check
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_validate_campaign_organization_rejects_cross_org_campaign(
    dcs_campaign, django_user_model
):
    user = django_user_model.objects.create_user(username="gq-create-writer")
    request = _request(
        user, auth=_token("ROLE_GQ_WRITER", default_organization="gq"), method="POST"
    )

    assert validate_campaign_organization(request, dcs_campaign) is False


@pytest.mark.django_db
def test_validate_campaign_organization_allows_same_org_campaign(
    dcs_campaign, django_user_model
):
    user = django_user_model.objects.create_user(username="dcs-create-writer")
    request = _request(
        user, auth=_token("ROLE_DCS_WRITER", default_organization="dcs"), method="POST"
    )

    assert validate_campaign_organization(request, dcs_campaign) is True


@pytest.mark.django_db
def test_validate_campaign_organization_allows_none_campaign(django_user_model):
    """No campaign in the payload yet (e.g. a partial PATCH) -- nothing to
    validate, defers to other required-field validation."""
    user = django_user_model.objects.create_user(username="dcs-none-writer")
    request = _request(
        user, auth=_token("ROLE_DCS_WRITER", default_organization="dcs"), method="POST"
    )

    assert validate_campaign_organization(request, None) is True
