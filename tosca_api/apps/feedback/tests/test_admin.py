"""Tests for GeoFeedbackAdmin's org-scoped queryset.

Closes a cross-org admin regression introduced by security tickets ticket
06: before that ticket, Django's default `ModelAdmin.has_*_permission`
(reading `request.user.has_perm()`) was always False for non-superusers, so
this admin was superuser-only in practice. Once `OrgRolePermissionBackend`
made `has_perm()` meaningful for `feedback.*_geofeedback` (it's in
`TOSCA_PERMISSION_MODELS`), any staff user with a WRITER+ role in *any* org
entitled to `feedback` could see/edit *every* organization's GeoFeedback
rows -- this admin never had a queryset scope of its own the way
Campaign/GeoStory/Event/Workspace do via `OrgScopedAdminMixin`. See the note
on `GeoFeedbackAdmin` for why the fix is a narrow `get_queryset` scope
rather than full `OrgScopedAdminMixin` adoption.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.feedback.admin import GeoFeedbackAdmin
from tosca_api.apps.feedback.models import GeoFeedback
from tosca_api.apps.organizations.models import Organization


def _token(*roles, default_organization=None):
    payload = {"realm_access": {"roles": list(roles)}}
    if default_organization is not None:
        payload["default_organization"] = default_organization
    return payload


def _request(user, auth=None):
    request = RequestFactory().get("/fake/")
    request.user = user
    request.auth = auth
    return request


@pytest.fixture
def orgs(db):
    dcs, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    gq, _ = Organization.objects.get_or_create(slug="gq", defaults={"name": "GQ"})
    return dcs, gq


@pytest.fixture
def feedback_admin():
    return GeoFeedbackAdmin(GeoFeedback, AdminSite())


@pytest.mark.django_db
def test_get_queryset_scopes_to_callers_org(django_user_model, orgs, feedback_admin):
    dcs, gq = orgs
    staff_user = django_user_model.objects.create_user(username="dcs-feedback-staff", is_staff=True)
    dcs_campaign = Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=staff_user)
    gq_campaign = Campaign.objects.create(organization=gq, title="GQ campaign", created_by=staff_user)
    dcs_feedback = GeoFeedback.objects.create(
        campaign=dcs_campaign, title="DCS feedback", created_by=staff_user
    )
    GeoFeedback.objects.create(campaign=gq_campaign, title="GQ feedback", created_by=staff_user)

    request = _request(staff_user, auth=_token("ROLE_DCS_WRITER", default_organization="dcs"))
    qs = feedback_admin.get_queryset(request)

    assert list(qs) == [dcs_feedback]


@pytest.mark.django_db
def test_get_queryset_cross_org_row_is_absent_not_just_unlisted(django_user_model, orgs, feedback_admin):
    """The queryset is the tenant gate -- a cross-org row must be genuinely
    absent (so admin's get_object -> 404s), not merely excluded from list
    ordering."""
    dcs, gq = orgs
    staff_user = django_user_model.objects.create_user(username="dcs-feedback-staff2", is_staff=True)
    gq_campaign = Campaign.objects.create(organization=gq, title="GQ campaign", created_by=staff_user)
    gq_feedback = GeoFeedback.objects.create(
        campaign=gq_campaign, title="GQ feedback", created_by=staff_user
    )

    request = _request(staff_user, auth=_token("ROLE_DCS_ADMIN", default_organization="dcs"))
    qs = feedback_admin.get_queryset(request)

    assert not qs.filter(pk=gq_feedback.pk).exists()


@pytest.mark.django_db
def test_get_queryset_unscoped_for_superuser(django_user_model, orgs, feedback_admin):
    dcs, gq = orgs
    superuser = django_user_model.objects.create_superuser(
        username="root-feedback", email="root@example.com", password="x"
    )
    dcs_campaign = Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=superuser)
    gq_campaign = Campaign.objects.create(organization=gq, title="GQ campaign", created_by=superuser)
    GeoFeedback.objects.create(campaign=dcs_campaign, title="DCS feedback", created_by=superuser)
    GeoFeedback.objects.create(campaign=gq_campaign, title="GQ feedback", created_by=superuser)

    qs = feedback_admin.get_queryset(_request(superuser))

    assert qs.count() == 2


@pytest.mark.django_db
def test_get_queryset_unscoped_for_exempt_platform_roles(django_user_model, orgs, feedback_admin):
    """DJANGO_STAFF/DJANGO_SUPERADMIN are exempt from org scoping everywhere
    else in this app (canonical §2b) -- confirm that stays true here too,
    rather than accidentally locking staff-only users out."""
    dcs, gq = orgs
    staff_user = django_user_model.objects.create_user(username="django-staff-feedback", is_staff=True)
    dcs_campaign = Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=staff_user)
    gq_campaign = Campaign.objects.create(organization=gq, title="GQ campaign", created_by=staff_user)
    GeoFeedback.objects.create(campaign=dcs_campaign, title="DCS feedback", created_by=staff_user)
    GeoFeedback.objects.create(campaign=gq_campaign, title="GQ feedback", created_by=staff_user)

    request = _request(staff_user, auth=_token("DJANGO_STAFF"))
    qs = feedback_admin.get_queryset(request)

    assert qs.count() == 2


@pytest.mark.django_db
def test_get_queryset_empty_without_org_role(django_user_model, orgs, feedback_admin):
    dcs, _gq = orgs
    staff_user = django_user_model.objects.create_user(username="no-role-feedback-staff", is_staff=True)
    dcs_campaign = Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=staff_user)
    GeoFeedback.objects.create(campaign=dcs_campaign, title="DCS feedback", created_by=staff_user)

    request = _request(staff_user, auth=_token())
    qs = feedback_admin.get_queryset(request)

    assert qs.count() == 0
