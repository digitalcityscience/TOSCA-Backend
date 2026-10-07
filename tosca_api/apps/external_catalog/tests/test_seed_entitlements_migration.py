"""Correctness of 0002_seed_external_catalog_entitlements (external catalog
ticket 01): existing organizations get the new app, nothing else changes.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as real_apps

from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

_migration = importlib.import_module(
    "tosca_api.apps.external_catalog.migrations.0002_seed_external_catalog_entitlements"
)


def _app_labels(org) -> set[str]:
    return set(
        OrganizationAppEntitlement.objects.filter(organization=org).values_list("app_label", flat=True)
    )


@pytest.mark.django_db
def test_seed_entitles_every_existing_org_to_external_catalog():
    org_a = Organization.objects.create(name="A", slug="org-a")
    org_b = Organization.objects.create(name="B", slug="org-b")
    OrganizationAppEntitlement.objects.create(organization=org_a, app_label="campaigns")

    _migration.seed_external_catalog_entitlements(real_apps, None)

    assert _app_labels(org_a) == {"campaigns", "external_catalog"}
    assert _app_labels(org_b) == {"external_catalog"}


@pytest.mark.django_db
def test_seed_is_idempotent():
    org = Organization.objects.create(name="A", slug="org-a")

    _migration.seed_external_catalog_entitlements(real_apps, None)
    _migration.seed_external_catalog_entitlements(real_apps, None)

    assert OrganizationAppEntitlement.objects.filter(organization=org, app_label="external_catalog").count() == 1


@pytest.mark.django_db
def test_unseed_removes_only_external_catalog_rows():
    org = Organization.objects.create(name="A", slug="org-a")
    OrganizationAppEntitlement.objects.create(organization=org, app_label="campaigns")
    _migration.seed_external_catalog_entitlements(real_apps, None)

    _migration.unseed(real_apps, None)

    assert _app_labels(org) == {"campaigns"}
