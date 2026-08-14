"""
Campaign/GeoStory archive & restore lifecycle (epic-11 PR3, §3.3/§7 of
``docs/development/epic-11-campaign-ownership-visibility-garage-lifecycle-14082026.md``).

When a Campaign is archived, every asset owned by that campaign should move
into the archive bucket. When a single GeoStory is archived (but its
Campaign is not), only that story's assets move -- the rest of the
campaign's assets stay wherever ``Campaign.visibility`` says they belong.
Restoring (un-archiving) moves assets back to the private/public bucket that
matches the *current* ``Campaign.visibility`` -- not necessarily the bucket
they started in, since visibility may have changed while archived.

Desired-bucket resolution, in priority order, for a given ``MediaAsset``:

1. **Campaign archived** -- the whole campaign is archived -> ``media_archive``.
2. **Owning GeoStory archived** -- the asset resolves (via
   ``media_paths.resolve_entity``) to a GeoStory whose own ``status`` is
   ``ARCHIVED`` -> ``media_archive``, even though the campaign itself may
   still be active.
3. **Otherwise** -- ``media_public`` when ``Campaign.visibility`` is public,
   else ``default`` (private).

Events have no archived status of their own (``Event.Status`` has no
``ARCHIVED`` member -- only Campaign/GeoStory do per §3.1 of the ticket), so
an Event-scoped asset only archives when its *Campaign* archives.

Known caveat -- GeoStory hero images: ``GeoStory.hero_image`` is a Django
``ImageField`` bound to the ``default`` storage at the model-field level
(``core.models`` / ``geostories.models.geostory_hero_image_upload_to`` does
not pass a ``storage=`` override). Physically copying that object's bytes to
another bucket without also rewriting the field's storage backend would
break ``hero_image.url``. This module therefore leaves hero-image-matched
assets' bytes and ``storage_alias`` untouched -- they are not currently
routed through per-alias storage the way EditorJS/misc uploads are. This is
a known scope boundary, not an oversight; revisit if/when hero images move
onto the same ``storages[alias]`` routing as everything else.

This module only performs the *move* (copy -> update DB -> delete old
object), mirroring ``media_path_migration.MediaPathMigrator``'s
copy-then-verify-then-delete safety property. It does not rewrite
``storage_path`` -- only ``storage_alias`` changes; the same canonical key
under PR2's scheme is reused across buckets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from django.core.files.base import ContentFile
from django.db import transaction

from tosca_api.apps.core.media_paths import KIND_STORY, resolve_entity

# Planned/observed actions.
ACTION_MOVED = "moved"
ACTION_NO_CHANGE = "no-change"
ACTION_SKIPPED_HERO_IMAGE = "skipped-hero-image"
ACTION_FAILED = "failed"

STATUS_OK = "ok"
STATUS_FAILED = "failed"

# Resolves a storage-alias name to a Django Storage instance.
StorageResolver = Callable[[str], "object"]


@dataclass
class LifecycleEntry:
    asset_id: str
    old_alias: str
    new_alias: str
    action: str
    status: str
    detail: str = ""


def _is_hero_image_match(asset) -> bool:
    """True when ``asset`` is the currently-set hero image of some GeoStory."""
    from tosca_api.apps.geostories.models import GeoStory

    return GeoStory.objects.filter(hero_image=asset.storage_path).exists()


def desired_alias_for_asset(asset) -> str | None:
    """Resolve the storage alias ``asset`` should live in right now.

    Returns ``None`` when the asset has no campaign (orphan -- nothing to
    key a lifecycle decision off, matches PR2's ``resolve_entity`` contract).
    """
    if asset.campaign_id is None:
        return None

    campaign = asset.campaign
    if campaign.status == campaign.Status.ARCHIVED:
        return asset.__class__.StorageAlias.ARCHIVE

    resolved = resolve_entity(asset)
    if resolved is not None and resolved.kind == KIND_STORY:
        from tosca_api.apps.geostories.models import GeoStory

        story = GeoStory.objects.filter(id=resolved.entity_id).only("status").first()
        if story is not None and story.status == GeoStory.Status.ARCHIVED:
            return asset.__class__.StorageAlias.ARCHIVE

    if campaign.visibility == campaign.Visibility.PUBLIC:
        return asset.__class__.StorageAlias.PUBLIC
    return asset.__class__.StorageAlias.DEFAULT


class MediaLifecycleService:
    """Moves ``MediaAsset`` objects between storage buckets on status change."""

    def __init__(self, *, storage_for_alias: StorageResolver) -> None:
        self._storage_for_alias = storage_for_alias

    def move_one(self, asset, target_alias: str) -> LifecycleEntry:
        """Move a single asset's object to ``target_alias`` if not already there.

        Copy -> verify size -> update DB ``storage_alias`` -> delete old
        object, matching ``MediaPathMigrator.apply_one``'s safety ordering:
        the source is never deleted until the destination copy is verified,
        so an interrupted run leaves the asset reachable at its old bucket.
        """
        old_alias = asset.storage_alias
        path = asset.storage_path

        if _is_hero_image_match(asset):
            return LifecycleEntry(
                str(asset.id), old_alias, old_alias, ACTION_SKIPPED_HERO_IMAGE, STATUS_OK,
                "hero_image is pinned to the default storage field -- not moved",
            )

        if old_alias == target_alias:
            return LifecycleEntry(str(asset.id), old_alias, target_alias, ACTION_NO_CHANGE, STATUS_OK)

        try:
            source_storage = self._storage_for_alias(old_alias)
            dest_storage = self._storage_for_alias(target_alias)

            if not source_storage.exists(path):
                return LifecycleEntry(
                    str(asset.id), old_alias, target_alias, ACTION_FAILED, STATUS_FAILED,
                    "source object missing at old_alias",
                )
            source_size = source_storage.size(path)

            if dest_storage.exists(path):
                dest_size = dest_storage.size(path)
                if dest_size != source_size:
                    return LifecycleEntry(
                        str(asset.id), old_alias, target_alias, ACTION_FAILED, STATUS_FAILED,
                        f"destination already exists with mismatched size "
                        f"({dest_size} != {source_size})",
                    )
                # Already copied (e.g. a prior interrupted run) -- proceed to
                # DB update + source delete.
            else:
                with source_storage.open(path, "rb") as handle:
                    data = handle.read()
                dest_storage.save(path, ContentFile(data, name=path))
                dest_size = dest_storage.size(path)
                if dest_size != source_size:
                    return LifecycleEntry(
                        str(asset.id), old_alias, target_alias, ACTION_FAILED, STATUS_FAILED,
                        f"post-copy size {dest_size} != source {source_size}",
                    )

            with transaction.atomic():
                asset.storage_alias = target_alias
                asset.save(update_fields=["storage_alias"])

            source_storage.delete(path)
            return LifecycleEntry(str(asset.id), old_alias, target_alias, ACTION_MOVED, STATUS_OK)
        except Exception as exc:  # partial-failure tolerance: record and continue
            return LifecycleEntry(
                str(asset.id), old_alias, target_alias, ACTION_FAILED, STATUS_FAILED, repr(exc)
            )

    def _sync_assets(self, assets: Iterable) -> list[LifecycleEntry]:
        entries = []
        for asset in assets:
            target = desired_alias_for_asset(asset)
            if target is None:
                continue
            entries.append(self.move_one(asset, target))
        return entries

    def sync_campaign_assets(self, campaign) -> list[LifecycleEntry]:
        """Re-evaluate and move every asset owned by ``campaign``."""
        return self._sync_assets(campaign.media_assets.select_related("campaign__organization").all())

    def sync_story_assets(self, story) -> list[LifecycleEntry]:
        """Re-evaluate and move only the assets that resolve to ``story``.

        Scans the owning campaign's assets (there is no direct
        ``MediaAsset -> GeoStory`` FK -- PR2's ``resolve_entity`` is the
        single source of truth for that mapping) and moves the subset whose
        resolved entity is this story.
        """
        if story.campaign_id is None:
            return []
        assets = story.campaign.media_assets.select_related("campaign__organization").all()
        entries = []
        for asset in assets:
            resolved = resolve_entity(asset)
            if resolved is None or resolved.kind != KIND_STORY or resolved.entity_id != str(story.id):
                continue
            target = desired_alias_for_asset(asset)
            if target is None:
                continue
            entries.append(self.move_one(asset, target))
        return entries


def summarize(entries: list[LifecycleEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.action] = counts.get(entry.action, 0) + 1
    return counts
