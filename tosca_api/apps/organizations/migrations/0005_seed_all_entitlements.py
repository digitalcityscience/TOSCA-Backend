"""Seed every existing organization with all in-scope app entitlements.

Security tickets ticket 03 hard gate: after deployment, no existing
organization may lose access due to entitlement -- gate B has no real
enforcement yet, so every organization gets every entitleable app.
"""

from django.conf import settings
from django.db import migrations


def seed_all_entitlements(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    OrganizationAppEntitlement = apps.get_model("organizations", "OrganizationAppEntitlement")

    entitlements = [
        OrganizationAppEntitlement(organization=organization, app_label=app_label)
        for organization in Organization.objects.all()
        for app_label in settings.TOSCA_ENTITLEABLE_APPS
    ]
    OrganizationAppEntitlement.objects.bulk_create(entitlements, ignore_conflicts=True)


def unseed(apps, schema_editor):
    OrganizationAppEntitlement = apps.get_model("organizations", "OrganizationAppEntitlement")
    OrganizationAppEntitlement.objects.filter(
        app_label__in=settings.TOSCA_ENTITLEABLE_APPS
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0004_organizationappentitlement"),
    ]

    operations = [
        migrations.RunPython(seed_all_entitlements, unseed),
    ]
