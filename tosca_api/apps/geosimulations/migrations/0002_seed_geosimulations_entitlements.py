"""Entitle every existing organization to the new ``geosimulations`` app.

``organizations.0005_seed_all_entitlements`` read ``TOSCA_ENTITLEABLE_APPS``
at the time it ran, so on existing databases it never saw this app. Same
rule as that migration (security tickets ticket 03): gate B has no real
per-org restriction yet, so every organization gets the app. Only this
app's rows are touched, in both directions.
"""

from django.db import migrations

APP_LABEL = "geosimulations"


def seed_geosimulations_entitlements(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    OrganizationAppEntitlement = apps.get_model("organizations", "OrganizationAppEntitlement")

    OrganizationAppEntitlement.objects.bulk_create(
        [
            OrganizationAppEntitlement(organization=organization, app_label=APP_LABEL)
            for organization in Organization.objects.all()
        ],
        ignore_conflicts=True,
    )


def unseed(apps, schema_editor):
    OrganizationAppEntitlement = apps.get_model("organizations", "OrganizationAppEntitlement")
    OrganizationAppEntitlement.objects.filter(app_label=APP_LABEL).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("geosimulations", "0001_initial"),
        ("organizations", "0005_seed_all_entitlements"),
    ]

    operations = [
        migrations.RunPython(seed_geosimulations_entitlements, unseed),
    ]
