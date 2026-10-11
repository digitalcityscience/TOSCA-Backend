"""Gate A/B wiring for GeoSimulations (Task 12.1).

The app's models are role-controlled and entitleable like every other app:
``has_perm()`` comes from (org role ∩ app entitlement ∩ TOSCA_PERMISSION_MODELS).
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as real_apps
from django.conf import settings

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.organizations.auth_backend import OrgRolePermissionBackend
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

from .helpers import org

MODELS = (
    "simulationmodel", "simulationrun", "simulationcomparison", "simulationoutput", "simulationasset",
    "simulationrunner", "simulationjob",
)

_migration = importlib.import_module(
    "tosca_api.apps.geosimulations.migrations.0002_seed_geosimulations_entitlements"
)


def _user_with_role(django_user_model, level, org_slug="dcs"):
    user = django_user_model.objects.create_user(username=f"{org_slug}-{level.lower()}")
    user._auth_claims = AuthClaims(org_roles={org_slug: level}, default_org=org_slug, authoritative=True)
    return user


def test_app_is_role_controlled_and_entitleable():
    assert settings.TOSCA_PERMISSION_MODELS["geosimulations"] == set(MODELS)
    assert "geosimulations" in settings.TOSCA_ENTITLEABLE_APPS


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("level", "allowed"),
    [
        ("READER", {"view"}),
        ("WRITER", {"view", "add", "change"}),
        ("ADMIN", {"view", "add", "change", "delete"}),
    ],
)
def test_role_levels_map_to_actions_on_every_model(django_user_model, level, allowed):
    org("dcs")
    user = _user_with_role(django_user_model, level)
    backend = OrgRolePermissionBackend()

    for model in MODELS:
        for action in ("view", "add", "change", "delete"):
            permission = f"geosimulations.{action}_{model}"
            assert backend.has_perm(user, permission) is (action in allowed), permission


@pytest.mark.django_db
def test_without_the_entitlement_nothing_is_allowed(django_user_model):
    organization, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    OrganizationAppEntitlement.objects.filter(
        organization=organization, app_label="geosimulations"
    ).delete()
    user = _user_with_role(django_user_model, "ADMIN")

    assert not OrgRolePermissionBackend().has_perm(user, "geosimulations.view_simulationrun")


@pytest.mark.django_db
def test_seed_migration_entitles_existing_organizations_only_for_this_app():
    org_a = Organization.objects.create(name="A", slug="org-a")
    org_b = Organization.objects.create(name="B", slug="org-b")
    OrganizationAppEntitlement.objects.create(organization=org_a, app_label="campaigns")

    _migration.seed_geosimulations_entitlements(real_apps, None)
    _migration.seed_geosimulations_entitlements(real_apps, None)

    def labels(organization):
        return set(
            OrganizationAppEntitlement.objects.filter(organization=organization).values_list(
                "app_label", flat=True
            )
        )

    assert labels(org_a) == {"campaigns", "geosimulations"}
    assert labels(org_b) == {"geosimulations"}

    _migration.unseed(real_apps, None)
    assert labels(org_a) == {"campaigns"}
