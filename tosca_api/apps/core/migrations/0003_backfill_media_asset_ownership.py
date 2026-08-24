"""Backfill MediaAsset.owner_org/campaign for rows that predate ownership fields.

This migration deliberately uses Django's historical app registry instead of
``core.media_ownership``. Importing the runtime helper would make this old data
migration query today's model fields, including columns created by later
migrations, and would prevent a database at this migration's state from being
upgraded.
"""

from django.db import migrations


def _iter_storage_urls(content):
    for block in (content or {}).get("blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        file_data = (block.get("data") or {}).get("file") or {}
        url = file_data.get("url")
        if isinstance(url, str) and url:
            yield url


def _url_references_storage_path(url, storage_path):
    return url.rstrip("/").endswith(storage_path.lstrip("/"))


def backfill_media_asset_ownership(apps, schema_editor):
    Campaign = apps.get_model("campaigns", "Campaign")
    MediaAsset = apps.get_model("core", "MediaAsset")
    Event = apps.get_model("events", "Event")
    EventSeries = apps.get_model("events", "EventSeries")
    GeoContext = apps.get_model("geocontext", "GeoContext")
    GeoStory = apps.get_model("geostories", "GeoStory")

    campaign_org_by_id = dict(Campaign.objects.values_list("id", "organization_id").iterator())

    # Keep the original matching priority: GeoStory, Event, EventSeries.
    context_campaign = {}
    for model, context_field in (
        (GeoStory, "context_id"),
        (Event, "context_id"),
        (EventSeries, "default_context_id"),
    ):
        rows = model.objects.exclude(**{f"{context_field}__isnull": True}).values_list(
            context_field, "campaign_id"
        )
        for context_id, campaign_id in rows.iterator():
            context_campaign.setdefault(context_id, campaign_id)

    hero_index = {
        hero_image: campaign_id
        for hero_image, campaign_id in GeoStory.objects.exclude(hero_image="").values_list(
            "hero_image", "campaign_id"
        )
        if hero_image
    }

    content_index = []
    for context_id, content in GeoContext.objects.values_list("id", "content").iterator():
        content_index.extend((url, context_id) for url in _iter_storage_urls(content))

    resolved = []
    assets = MediaAsset.objects.filter(campaign__isnull=True).values_list("id", "storage_path")
    for asset_id, storage_path in assets.iterator():
        campaign_id = hero_index.get(storage_path)
        if campaign_id is None:
            context_id = next(
                (
                    candidate_context_id
                    for url, candidate_context_id in content_index
                    if _url_references_storage_path(url, storage_path)
                ),
                None,
            )
            campaign_id = context_campaign.get(context_id)

        if campaign_id is not None:
            resolved.append((asset_id, campaign_id, campaign_org_by_id.get(campaign_id)))

    batch_size = 500
    for start in range(0, len(resolved), batch_size):
        batch = resolved[start : start + batch_size]
        assets_by_id = {
            asset.id: asset
            for asset in MediaAsset.objects.filter(
                id__in=[asset_id for asset_id, _, _ in batch]
            ).only("id", "campaign", "owner_org")
        }
        to_update = []
        for asset_id, campaign_id, organization_id in batch:
            asset = assets_by_id.get(asset_id)
            if asset is None or asset.campaign_id is not None:
                continue
            asset.campaign_id = campaign_id
            asset.owner_org_id = organization_id
            to_update.append(asset)

        if to_update:
            MediaAsset.objects.bulk_update(to_update, ["campaign", "owner_org"])


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
