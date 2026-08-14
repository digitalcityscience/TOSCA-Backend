"""Seed the default 'dcs' Organization and backfill existing rows onto it.

Epic 11 part 1 (A2): Workspace/Campaign gained a nullable ``organization`` FK.
This migration creates the seed 'dcs' org and attaches every pre-existing row to
it, so the follow-up migration can safely enforce ``null=False``.
"""

from django.db import migrations

SEED_SLUG = "dcs"
SEED_NAME = "DCS"


def seed_and_backfill(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Workspace = apps.get_model("geodata_providers", "Workspace")
    Campaign = apps.get_model("campaigns", "Campaign")

    dcs, _ = Organization.objects.get_or_create(
        slug=SEED_SLUG,
        defaults={"name": SEED_NAME, "is_active": True},
    )

    Workspace.objects.filter(organization__isnull=True).update(organization=dcs)
    Campaign.objects.filter(organization__isnull=True).update(organization=dcs)


def unseed(apps, schema_editor):
    # Detach rows; leave the org row in place (removing it could orphan FKs from
    # other reverse relations added later). Backfill is a data-only concern.
    Workspace = apps.get_model("geodata_providers", "Workspace")
    Campaign = apps.get_model("campaigns", "Campaign")
    Organization = apps.get_model("organizations", "Organization")

    dcs = Organization.objects.filter(slug=SEED_SLUG).first()
    if dcs is None:
        return
    Workspace.objects.filter(organization=dcs).update(organization=None)
    Campaign.objects.filter(organization=dcs).update(organization=None)


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0001_initial"),
        ("geodata_providers", "0009_workspace_organization_workspace_visibility"),
        ("campaigns", "0003_campaign_organization"),
    ]

    operations = [
        migrations.RunPython(seed_and_backfill, unseed),
    ]
