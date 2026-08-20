"""Backfill MediaAsset.owner_org/campaign for rows that predate ownership fields.

Best-effort data migration wrapping ``core.media_ownership``. Safe on an
empty table (no MediaAsset rows -> no-op) and re-runnable (only ever touches
rows where campaign is still null).
"""

from django.db import migrations


def backfill_media_asset_ownership(apps, schema_editor):
    from tosca_api.apps.core.media_ownership import apply_backfill, plan_backfill

    entries = plan_backfill()
    apply_backfill(entries)


def noop_reverse(apps, schema_editor):
    # Deliberately not reversible: unsetting campaign/owner_org on reverse
    # would discard real assignments a later migration or manual fix may
    # have layered on top. Matches this migration's `noop_reverse` sibling
    # pattern used elsewhere in the codebase for backfill migrations.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_mediaasset_campaign_mediaasset_owner_org_and_more"),
        ("geostories", "0005_alter_geostorylayer_unique_together_and_more"),
        ("events", "0021_eventlayer_updated_at_alter_eventseries_campaign"),
        ("geocontext", "0004_alter_geocontext_id"),
    ]

    operations = [
        migrations.RunPython(backfill_media_asset_ownership, noop_reverse),
    ]
