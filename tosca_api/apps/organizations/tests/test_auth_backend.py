"""Tests for OrgRolePermissionBackend (security tickets ticket 06).

Computes has_perm() dynamically from (org role ∩ app entitlement ∩
role-controlled models) -- no Permission/Group rows, ever. Claims are
attached directly via user._auth_claims (the ticket-05 resolver's live-claims
path) rather than going through a real login, matching how the backend
actually reads them at request time.
"""

from __future__ import annotations

import pytest

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.organizations.auth_backend import OrgRolePermissionBackend
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement


@pytest.fixture
def backend():
    return OrgRolePermissionBackend()


@pytest.fixture
def dcs(db):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    OrganizationAppEntitlement.objects.get_or_create(organization=org, app_label="campaigns")
    return org


def _user_with_role(django_user_model, level, org_slug="dcs", username="role-user"):
    user = django_user_model.objects.create_user(username=username)
    user._auth_claims = AuthClaims(
        org_roles={org_slug: level}, default_org=org_slug, authoritative=True
    )
    return user


# ---------------------------------------------------------------------------
# Role -> verb map (Layer A)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_reader_can_view_but_not_write(django_user_model, backend, dcs):
    user = _user_with_role(django_user_model, "READER")

    assert backend.has_perm(user, "campaigns.view_campaign") is True
    assert backend.has_perm(user, "campaigns.add_campaign") is False
    assert backend.has_perm(user, "campaigns.change_campaign") is False
    assert backend.has_perm(user, "campaigns.delete_campaign") is False


@pytest.mark.django_db
def test_writer_can_view_add_change_not_delete(django_user_model, backend, dcs):
    user = _user_with_role(django_user_model, "WRITER")

    assert backend.has_perm(user, "campaigns.view_campaign") is True
    assert backend.has_perm(user, "campaigns.add_campaign") is True
    assert backend.has_perm(user, "campaigns.change_campaign") is True
    assert backend.has_perm(user, "campaigns.delete_campaign") is False


@pytest.mark.django_db
def test_admin_can_do_everything(django_user_model, backend, dcs):
    user = _user_with_role(django_user_model, "ADMIN")

    assert backend.has_perm(user, "campaigns.view_campaign") is True
    assert backend.has_perm(user, "campaigns.add_campaign") is True
    assert backend.has_perm(user, "campaigns.change_campaign") is True
    assert backend.has_perm(user, "campaigns.delete_campaign") is True


# ---------------------------------------------------------------------------
# Gate B: entitlement
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_entitlement_missing_denies_even_admin(django_user_model, backend, db):
    org, _ = Organization.objects.get_or_create(slug="qg2", defaults={"name": "QG2"})
    # Deliberately no OrganizationAppEntitlement row for "campaigns".
    user = _user_with_role(django_user_model, "ADMIN", org_slug="qg2")

    assert backend.has_perm(user, "campaigns.view_campaign") is False


@pytest.mark.django_db
def test_entitlement_present_for_a_different_app_does_not_leak(django_user_model, backend, db):
    org, _ = Organization.objects.get_or_create(slug="qg3", defaults={"name": "QG3"})
    OrganizationAppEntitlement.objects.create(organization=org, app_label="events")
    user = _user_with_role(django_user_model, "ADMIN", org_slug="qg3")

    assert backend.has_perm(user, "campaigns.view_campaign") is False
    assert backend.has_perm(user, "events.view_event") is True


# ---------------------------------------------------------------------------
# Model not in the role-controlled allow-list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_model_not_allowlisted_denied(django_user_model, backend, dcs):
    user = _user_with_role(django_user_model, "ADMIN")

    # "campaignrevision" is not in TOSCA_PERMISSION_MODELS["campaigns"].
    assert backend.has_perm(user, "campaigns.view_campaignrevision") is False


@pytest.mark.django_db
def test_unknown_app_label_denied(django_user_model, backend, dcs):
    user = _user_with_role(django_user_model, "ADMIN")

    assert backend.has_perm(user, "not_a_real_app.view_thing") is False


@pytest.mark.django_db
def test_custom_permission_action_excluded(django_user_model, backend, dcs):
    """Custom perms (e.g. `publish_*`) are never granted by this backend,
    no matter how high the caller's role."""
    user = _user_with_role(django_user_model, "ADMIN")

    assert backend.has_perm(user, "campaigns.publish_campaign") is False


# ---------------------------------------------------------------------------
# Fail closed / edge cases
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_no_claims_at_all_fails_closed(django_user_model, backend, dcs):
    user = django_user_model.objects.create_user(username="no-claims-user")

    assert backend.has_perm(user, "campaigns.view_campaign") is False


@pytest.mark.django_db
def test_no_role_for_default_org_denied(django_user_model, backend, dcs):
    user = django_user_model.objects.create_user(username="no-role-user")
    user._auth_claims = AuthClaims(org_roles={}, default_org="dcs", authoritative=True)

    assert backend.has_perm(user, "campaigns.view_campaign") is False


@pytest.mark.django_db
def test_superuser_bypasses_everything(django_user_model, backend, db):
    superuser = django_user_model.objects.create_superuser(
        username="root-authz", email="root@example.com", password="x"
    )

    assert backend.has_perm(superuser, "campaigns.view_campaign") is True
    assert backend.has_perm(superuser, "not_a_real_app.delete_thing") is True


@pytest.mark.django_db
def test_inactive_user_denied_even_as_superuser(django_user_model, backend, db):
    superuser = django_user_model.objects.create_superuser(
        username="root-inactive", email="root@example.com", password="x", is_active=False
    )

    assert backend.has_perm(superuser, "campaigns.view_campaign") is False


@pytest.mark.django_db
def test_inactive_regular_user_denied(django_user_model, backend, dcs):
    user = _user_with_role(django_user_model, "ADMIN")
    user.is_active = False

    assert backend.has_perm(user, "campaigns.view_campaign") is False


# ---------------------------------------------------------------------------
# End-to-end through Django's has_perm() dispatch (registered backend)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_registered_backend_makes_user_has_perm_meaningful(django_user_model, dcs):
    """Confirms the backend is actually wired into AUTHENTICATION_BACKENDS,
    not just directly callable -- this is what ticket 07's admin integration
    and (eventually) DRF resources will rely on."""
    user = _user_with_role(django_user_model, "WRITER")

    assert user.has_perm("campaigns.view_campaign") is True
    assert user.has_perm("campaigns.delete_campaign") is False


@pytest.mark.django_db
def test_registered_backend_fail_closed_for_plain_user(django_user_model, db):
    user = django_user_model.objects.create_user(username="plain-user")

    assert user.has_perm("campaigns.view_campaign") is False
