"""Gate A/B wiring for the external catalog (external catalog ticket 01).

The app's models must be role-controlled and entitleable exactly like the
existing apps: ``has_perm()`` is computed by ``OrgRolePermissionBackend``
from (org role ∩ app entitlement ∩ TOSCA_PERMISSION_MODELS).
"""

from __future__ import annotations

import pytest
from django.conf import settings

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.organizations.auth_backend import OrgRolePermissionBackend
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

MODELS = ("externalservice", "category", "categoryitem")


@pytest.fixture
def backend():
    return OrgRolePermissionBackend()


@pytest.fixture
def entitled_org(db):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    OrganizationAppEntitlement.objects.get_or_create(organization=org, app_label="external_catalog")
    return org


def _user_with_role(django_user_model, level, org_slug="dcs"):
    user = django_user_model.objects.create_user(username=f"{org_slug}-{level.lower()}")
    user._auth_claims = AuthClaims(org_roles={org_slug: level}, default_org=org_slug, authoritative=True)
    return user


def test_app_is_role_controlled_and_entitleable():
    assert settings.TOSCA_PERMISSION_MODELS["external_catalog"] == set(MODELS)
    assert "external_catalog" in settings.TOSCA_ENTITLEABLE_APPS


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("level", "allowed"),
    [
        ("READER", {"view"}),
        ("WRITER", {"view", "add", "change"}),
        ("ADMIN", {"view", "add", "change", "delete"}),
    ],
)
def test_role_levels_map_to_actions_on_every_model(django_user_model, backend, entitled_org, level, allowed):
    user = _user_with_role(django_user_model, level)

    for model in MODELS:
        for action in ("view", "add", "change", "delete"):
            permission = f"external_catalog.{action}_{model}"
            assert backend.has_perm(user, permission) is (action in allowed), permission


@pytest.mark.django_db
def test_missing_entitlement_denies_even_admin(django_user_model, backend, db):
    Organization.objects.get_or_create(slug="qg2", defaults={"name": "QG2"})
    user = _user_with_role(django_user_model, "ADMIN", org_slug="qg2")

    assert backend.has_perm(user, "external_catalog.view_category") is False
    assert backend.has_module_perms(user, "external_catalog") is False


@pytest.mark.django_db
def test_reader_sees_the_app_module_when_entitled(django_user_model, backend, entitled_org):
    user = _user_with_role(django_user_model, "READER")

    assert backend.has_module_perms(user, "external_catalog") is True
