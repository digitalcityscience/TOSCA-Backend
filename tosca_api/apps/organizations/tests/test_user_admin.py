"""Tests for the custom UserAdmin's read-only effective-authorization panel
(security tickets ticket 07). No Keycloak/GeoServer -- claims are attached
directly via user._auth_claims or a persisted UserAuthorizationSnapshot, the
same technique used by test_authz_resolver.py.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.organizations.admin import UserAdmin
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement, UserAuthorizationSnapshot

User = get_user_model()


@pytest.fixture
def user_admin():
    return UserAdmin(User, AdminSite())


@pytest.fixture
def solo_org(db):
    """A freshly created org entitled to *only* 'campaigns' -- unlike the
    migration-seeded 'dcs' org (entitled to everything), this keeps the
    effective-permissions assertions small and exact."""
    org = Organization.objects.create(name="Solo", slug="solo-org")
    OrganizationAppEntitlement.objects.create(organization=org, app_label="campaigns")
    return org


@pytest.mark.django_db
def test_user_admin_is_registered_in_place_of_the_default(user_admin):
    from django.contrib import admin

    assert isinstance(admin.site._registry[User], UserAdmin)


@pytest.mark.django_db
def test_effective_default_org_none_without_claims(django_user_model, user_admin):
    user = django_user_model.objects.create_user(username="no-claims-panel")

    assert user_admin.effective_default_org(user) == "(none)"


@pytest.mark.django_db
def test_effective_default_org_and_org_roles(django_user_model, user_admin, solo_org):
    user = django_user_model.objects.create_user(username="panel-writer")
    user._auth_claims = AuthClaims(
        org_roles={"solo-org": "WRITER"}, default_org="solo-org", authoritative=True
    )

    assert user_admin.effective_default_org(user) == "solo-org"
    assert user_admin.effective_org_roles(user) == "solo-org: WRITER"


@pytest.mark.django_db
def test_effective_platform_exempt_reflects_claim(django_user_model, user_admin):
    user = django_user_model.objects.create_user(username="panel-exempt")
    user._auth_claims = AuthClaims(org_roles={}, default_org=None, authoritative=True, platform_exempt=True)

    assert user_admin.effective_platform_exempt(user) is True


@pytest.mark.django_db
def test_effective_entitled_apps_for_default_org(django_user_model, user_admin, solo_org):
    user = django_user_model.objects.create_user(username="panel-entitled")
    user._auth_claims = AuthClaims(
        org_roles={"solo-org": "READER"}, default_org="solo-org", authoritative=True
    )

    assert user_admin.effective_entitled_apps(user) == "campaigns"


@pytest.mark.django_db
def test_effective_entitled_apps_no_default_org(django_user_model, user_admin):
    user = django_user_model.objects.create_user(username="panel-no-org")

    assert user_admin.effective_entitled_apps(user) == "(no default org)"


@pytest.mark.django_db
def test_effective_permissions_matches_writer_verb_set(django_user_model, user_admin, solo_org):
    user = django_user_model.objects.create_user(username="panel-perms-writer")
    user._auth_claims = AuthClaims(
        org_roles={"solo-org": "WRITER"}, default_org="solo-org", authoritative=True
    )

    perms = user_admin.effective_permissions(user)

    assert perms == "campaigns.add_campaign, campaigns.change_campaign, campaigns.view_campaign"


@pytest.mark.django_db
def test_effective_permissions_none_without_claims(django_user_model, user_admin):
    user = django_user_model.objects.create_user(username="panel-perms-none")

    assert user_admin.effective_permissions(user) == "(none)"


@pytest.mark.django_db
def test_effective_permissions_superuser(django_user_model, user_admin):
    superuser = django_user_model.objects.create_superuser(
        username="panel-superuser", email="root@example.com", password="x"
    )

    assert user_admin.effective_permissions(superuser) == "(superuser -- all permissions)"


@pytest.mark.django_db
def test_effective_synced_at_never_synced(django_user_model, user_admin):
    user = django_user_model.objects.create_user(username="panel-never-synced")

    assert user_admin.effective_synced_at(user) == "(never synced)"


@pytest.mark.django_db
def test_effective_synced_at_reflects_snapshot(django_user_model, user_admin):
    user = django_user_model.objects.create_user(username="panel-synced")
    snapshot = UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={"dcs": "ADMIN"}, default_org="dcs", synced_at=timezone.now()
    )

    assert user_admin.effective_synced_at(user) == snapshot.synced_at


@pytest.mark.django_db
def test_effective_authorization_fields_readonly_only_when_editing(user_admin):
    add_request = RequestFactory().get("/fake/add/")

    fields = user_admin.get_readonly_fields(add_request, obj=None)

    assert "effective_permissions" not in fields


@pytest.mark.django_db
def test_change_view_renders_effective_authorization_panel(client, django_user_model, user_admin, solo_org):
    superuser = django_user_model.objects.create_superuser(
        username="panel-http-viewer", email="root@example.com", password="x"
    )
    target = django_user_model.objects.create_user(username="panel-http-target")
    target._auth_claims = AuthClaims(
        org_roles={"solo-org": "ADMIN"}, default_org="solo-org", authoritative=True
    )
    from tosca_api.apps.organizations.policy import sync_snapshot

    sync_snapshot(target, target._auth_claims)
    client.force_login(superuser)

    response = client.get(f"/admin/auth/user/{target.pk}/change/")

    assert response.status_code == 200
    assert b"Effective authorization" in response.content
    assert b"solo-org" in response.content
    assert b"campaigns.delete_campaign" in response.content
