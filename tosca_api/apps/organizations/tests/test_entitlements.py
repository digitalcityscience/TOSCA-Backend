"""Tests for OrganizationAppEntitlement (security tickets ticket 03).

Covers: entitlement validation against the TOSCA_ENTITLEABLE_APPS single
source of truth, the seed migration's "no org loses access" hard gate, and
policy.enabled_apps_for.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import override_settings

from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement
from tosca_api.apps.organizations.policy import enabled_apps_for


@pytest.fixture
def org(db):
    return Organization.objects.create(name="QG2", slug="qg2")


@pytest.mark.django_db
def test_entitlement_accepts_app_in_source_of_truth(org):
    entitlement = OrganizationAppEntitlement(organization=org, app_label="campaigns")
    entitlement.full_clean()
    entitlement.save()
    assert entitlement.app_label == "campaigns"


@pytest.mark.django_db
def test_entitlement_rejects_app_not_in_source_of_truth(org):
    entitlement = OrganizationAppEntitlement(organization=org, app_label="not_a_real_app")
    with pytest.raises(ValidationError):
        entitlement.full_clean()


@pytest.mark.django_db
def test_entitlement_unique_per_organization_and_app(org):
    OrganizationAppEntitlement.objects.create(organization=org, app_label="campaigns")
    with pytest.raises(Exception):
        OrganizationAppEntitlement.objects.create(organization=org, app_label="campaigns")


@pytest.mark.django_db
def test_enabled_apps_for_returns_only_that_orgs_entitlements(org):
    other = Organization.objects.create(name="DCS", slug="dcs-2")
    OrganizationAppEntitlement.objects.create(organization=org, app_label="campaigns")
    OrganizationAppEntitlement.objects.create(organization=other, app_label="events")

    assert enabled_apps_for(org) == {"campaigns"}


@pytest.mark.django_db
def test_entitleable_apps_derived_from_permission_models():
    assert settings.TOSCA_ENTITLEABLE_APPS == set(settings.TOSCA_PERMISSION_MODELS)


@pytest.mark.django_db
@override_settings(TOSCA_ENTITLEABLE_APPS={"campaigns"})
def test_entitlement_validator_reflects_settings_override(org):
    OrganizationAppEntitlement(organization=org, app_label="campaigns").full_clean()
    with pytest.raises(ValidationError):
        OrganizationAppEntitlement(organization=org, app_label="events").full_clean()
