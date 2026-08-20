"""Correctness test for the 0005_seed_all_entitlements data migration
(security tickets ticket 03 hard gate: no existing organization loses
access on deploy).
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as real_apps
from django.conf import settings

from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

_migration = importlib.import_module(
    "tosca_api.apps.organizations.migrations.0005_seed_all_entitlements"
)


@pytest.mark.django_db
def test_seed_all_entitlements_covers_every_org_and_every_entitleable_app():
    org_a = Organization.objects.create(name="A", slug="org-a")
    org_b = Organization.objects.create(name="B", slug="org-b")

    _migration.seed_all_entitlements(real_apps, None)

    for org in (org_a, org_b):
        app_labels = set(
            OrganizationAppEntitlement.objects.filter(organization=org).values_list(
                "app_label", flat=True
            )
        )
        assert app_labels == settings.TOSCA_ENTITLEABLE_APPS


@pytest.mark.django_db
def test_seed_all_entitlements_is_idempotent():
    org = Organization.objects.create(name="A", slug="org-a")

    _migration.seed_all_entitlements(real_apps, None)
    _migration.seed_all_entitlements(real_apps, None)

    assert OrganizationAppEntitlement.objects.filter(organization=org).count() == len(
        settings.TOSCA_ENTITLEABLE_APPS
    )


@pytest.mark.django_db
def test_unseed_removes_only_entitleable_app_rows():
    org = Organization.objects.create(name="A", slug="org-a")
    _migration.seed_all_entitlements(real_apps, None)

    _migration.unseed(real_apps, None)

    assert not OrganizationAppEntitlement.objects.filter(organization=org).exists()
