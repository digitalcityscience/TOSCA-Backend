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

Desired-bucket resolution, in priority order, for a given ``MediaAsset``
(security tickets S2 truth table):

1. **Campaign archived** -- the whole campaign is archived -> ``media_archive``.
2. **Owning GeoStory archived** -- the asset resolves (via
   ``media_paths.resolve_entity``) to a GeoStory whose own ``status`` is
   ``ARCHIVED`` -> ``media_archive``, even though the campaign itself may
   still be active.
3. **Otherwise** -- ``media_public`` iff ``Campaign.visibility`` is public
   **and** the resolved owning entity (GeoStory/Event) is itself published
   -- an asset with a known, unpublished entity stays private under a
   public campaign (this is the S2 gap: a public campaign alone used to be
   enough). A ``KIND_MISC`` asset (campaign-level, no single owning entity --
   e.g. ``EventSeries.default_content``) has no entity-publication axis to
   check, so campaign visibility alone decides it. Anything not public by
   this rule is ``default`` (private).

Events have no archived status of their own (``Event.Status`` has no
``ARCHIVED`` member -- only Campaign/GeoStory do per §3.1 of the ticket), so
an Event-scoped asset only archives when its *Campaign* archives.

GeoStory hero images use a dynamic ImageField storage backend selected by
``GeoStory.hero_image_storage_alias``. They therefore participate in the
same copy/update/delete lifecycle as EditorJS and misc media, including
stories that have no corresponding ``MediaAsset`` row.

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

from tosca_api.apps.core.media_paths import (
    KIND_EVENT,
    KIND_FEEDBACK,
    KIND_STORY,
    resolve_entity,
)

# Planned/observed actions.
ACTION_MOVED = "moved"
ACTION_NO_CHANGE = "no-change"
ACTION_SKIPPED_HERO_IMAGE = "skipped-hero-image"
ACTION_FAILED = "failed"
ACTION_WOULD_MOVE = "would-move"

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

    # KIND_MISC (campaign-level, no single owning entity) has no
    # publication axis of its own, so it defaults to "published" for the
    # purposes of the public-iff check below -- campaign visibility alone
    # decides it.
    resolved = resolve_entity(asset)
    entity_published = True

    if resolved is not None and resolved.kind == KIND_STORY:
        from tosca_api.apps.geostories.models import GeoStory

        story = GeoStory.objects.filter(id=resolved.entity_id).only("status").first()
        if story is not None:
            if story.status == GeoStory.Status.ARCHIVED:
                return asset.__class__.StorageAlias.ARCHIVE
            entity_published = story.status == GeoStory.Status.PUBLISHED
    elif resolved is not None and resolved.kind == KIND_EVENT:
        from tosca_api.apps.events.models import Event

        event = Event.objects.filter(id=resolved.entity_id).only("status").first()
        if event is not None:
            entity_published = event.status == Event.Status.PUBLISHED
    elif resolved is not None and resolved.kind == KIND_FEEDBACK:
        from tosca_api.apps.feedback.models import GeoFeedback

        feedback = (
            GeoFeedback.objects.filter(id=resolved.entity_id).only("status", "visibility").first()
        )
        if feedback is not None:
            entity_published = (
                feedback.status == GeoFeedback.Status.PUBLISHED
                and feedback.visibility == GeoFeedback.Visibility.PUBLIC
            )

    if campaign.visibility == campaign.Visibility.PUBLIC and entity_published:
        return asset.__class__.StorageAlias.PUBLIC
    return asset.__class__.StorageAlias.DEFAULT


class MediaLifecycleService:
    """Moves ``MediaAsset`` objects between storage buckets on status change."""

    def __init__(self, *, storage_for_alias: StorageResolver) -> None:
        self._storage_for_alias = storage_for_alias

    def move_one(self, asset, target_alias: str, *, dry_run: bool = False) -> LifecycleEntry:
        """Move a single asset's object to ``target_alias`` if not already there.

        Copy -> verify size -> update DB ``storage_alias`` -> delete old
        object, matching ``MediaPathMigrator.apply_one``'s safety ordering:
        the source is never deleted until the destination copy is verified,
        so an interrupted run leaves the asset reachable at its old bucket.

        ``dry_run=True`` reports what would happen (``ACTION_WOULD_MOVE`` or
        ``ACTION_NO_CHANGE``) without touching storage or the DB.
        """
        old_alias = asset.storage_alias
        path = asset.storage_path

        if _is_hero_image_match(asset):
            from tosca_api.apps.geostories.models import GeoStory

            story = GeoStory.objects.filter(hero_image=path).first()
            if story is not None:
                return self.move_hero_image(story, target_alias, dry_run=dry_run)

        if old_alias == target_alias:
            return LifecycleEntry(
                str(asset.id), old_alias, target_alias, ACTION_NO_CHANGE, STATUS_OK
            )

        try:
            source_storage = self._storage_for_alias(old_alias)
            dest_storage = self._storage_for_alias(target_alias)

            if not source_storage.exists(path):
                return LifecycleEntry(
                    str(asset.id),
                    old_alias,
                    target_alias,
                    ACTION_FAILED,
                    STATUS_FAILED,
                    "source object missing at old_alias",
                )
            source_size = source_storage.size(path)

            if dest_storage.exists(path):
                dest_size = dest_storage.size(path)
                if dest_size != source_size:
                    return LifecycleEntry(
                        str(asset.id),
                        old_alias,
                        target_alias,
                        ACTION_FAILED,
                        STATUS_FAILED,
                        f"destination already exists with mismatched size "
                        f"({dest_size} != {source_size})",
                    )
                # Already copied (e.g. a prior interrupted run) -- proceed to
                # DB update + source delete.
            elif dry_run:
                # Source verified, no pre-existing destination conflict --
                # this move would succeed, but stop before writing.
                return LifecycleEntry(
                    str(asset.id), old_alias, target_alias, ACTION_WOULD_MOVE, STATUS_OK
                )
            else:
                with source_storage.open(path, "rb") as handle:
                    data = handle.read()
                dest_storage.save(path, ContentFile(data, name=path))
                dest_size = dest_storage.size(path)
                if dest_size != source_size:
                    return LifecycleEntry(
                        str(asset.id),
                        old_alias,
                        target_alias,
                        ACTION_FAILED,
                        STATUS_FAILED,
                        f"post-copy size {dest_size} != source {source_size}",
                    )

            if dry_run:
                return LifecycleEntry(
                    str(asset.id), old_alias, target_alias, ACTION_WOULD_MOVE, STATUS_OK
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

    def move_hero_image(self, story, target_alias: str, *, dry_run: bool = False) -> LifecycleEntry:
        """Move a GeoStory hero image and persist its active storage alias."""
        from tosca_api.apps.core.models import MediaAsset

        hero = story.hero_image
        path = hero.name if hero else ""
        old_alias = story.hero_image_storage_alias
        if not path:
            return LifecycleEntry(
                f"hero:{story.id}",
                old_alias,
                target_alias,
                ACTION_NO_CHANGE,
                STATUS_OK,
                "story has no hero image",
            )
        if old_alias == target_alias:
            return LifecycleEntry(
                f"hero:{story.id}", old_alias, target_alias, ACTION_NO_CHANGE, STATUS_OK
            )

        try:
            source_storage = self._storage_for_alias(old_alias)
            dest_storage = self._storage_for_alias(target_alias)
            if not source_storage.exists(path):
                return LifecycleEntry(
                    f"hero:{story.id}",
                    old_alias,
                    target_alias,
                    ACTION_FAILED,
                    STATUS_FAILED,
                    "source hero image missing at old_alias",
                )
            source_size = source_storage.size(path)

            if dest_storage.exists(path):
                dest_size = dest_storage.size(path)
                if dest_size != source_size:
                    return LifecycleEntry(
                        f"hero:{story.id}",
                        old_alias,
                        target_alias,
                        ACTION_FAILED,
                        STATUS_FAILED,
                        f"destination already exists with mismatched size ({dest_size} != {source_size})",
                    )
            elif dry_run:
                return LifecycleEntry(
                    f"hero:{story.id}", old_alias, target_alias, ACTION_WOULD_MOVE, STATUS_OK
                )
            else:
                with source_storage.open(path, "rb") as handle:
                    dest_storage.save(path, ContentFile(handle.read(), name=path))
                if dest_storage.size(path) != source_size:
                    return LifecycleEntry(
                        f"hero:{story.id}",
                        old_alias,
                        target_alias,
                        ACTION_FAILED,
                        STATUS_FAILED,
                        "post-copy hero image size does not match source",
                    )

            if dry_run:
                return LifecycleEntry(
                    f"hero:{story.id}", old_alias, target_alias, ACTION_WOULD_MOVE, STATUS_OK
                )

            with transaction.atomic():
                story.hero_image_storage_alias = target_alias
                story.save(update_fields=["hero_image_storage_alias"])
                MediaAsset.objects.filter(storage_path=path).update(storage_alias=target_alias)

            source_storage.delete(path)
            return LifecycleEntry(
                f"hero:{story.id}", old_alias, target_alias, ACTION_MOVED, STATUS_OK
            )
        except Exception as exc:
            return LifecycleEntry(
                f"hero:{story.id}", old_alias, target_alias, ACTION_FAILED, STATUS_FAILED, repr(exc)
            )

    def _sync_assets(self, assets: Iterable, *, dry_run: bool = False) -> list[LifecycleEntry]:
        entries = []
        for asset in assets:
            target = desired_alias_for_asset(asset)
            if target is None:
                continue
            entries.append(self.move_one(asset, target, dry_run=dry_run))
        return entries

    def sync_campaign_assets(self, campaign, *, dry_run: bool = False) -> list[LifecycleEntry]:
        """Re-evaluate and move every asset owned by ``campaign``."""
        entries = [
            self.move_hero_image(story, story.desired_hero_image_storage_alias(), dry_run=dry_run)
            for story in campaign.geostories.all()
            if story.hero_image
        ]
        return entries + self._sync_assets(
            campaign.media_assets.select_related("campaign__organization").all(), dry_run=dry_run
        )

    def _sync_entity_assets(self, campaign, kind: str, entity_id) -> list[LifecycleEntry]:
        """Move the subset of ``campaign``'s assets that resolve to ``(kind, entity_id)``.

        Scans the owning campaign's assets (there is no direct
        ``MediaAsset -> GeoStory``/``Event`` FK -- PR2's ``resolve_entity`` is
        the single source of truth for that mapping) and moves only the
        assets whose resolved entity matches. Shared by ``sync_story_assets``
        and ``sync_event_assets``; a GeoStory additionally has a hero image,
        which its caller handles separately.
        """
        entries = []
        assets = campaign.media_assets.select_related("campaign__organization").all()
        for asset in assets:
            resolved = resolve_entity(asset)
            if resolved is None or resolved.kind != kind or resolved.entity_id != str(entity_id):
                continue
            target = desired_alias_for_asset(asset)
            if target is None:
                continue
            entries.append(self.move_one(asset, target))
        return entries

    def sync_story_assets(self, story) -> list[LifecycleEntry]:
        """Re-evaluate and move only the assets that resolve to ``story``."""
        if story.campaign_id is None:
            return []
        entries = []
        if story.hero_image:
            entries.append(self.move_hero_image(story, story.desired_hero_image_storage_alias()))
        entries += self._sync_entity_assets(story.campaign, KIND_STORY, story.id)
        return entries

    def sync_event_assets(self, event) -> list[LifecycleEntry]:
        """Re-evaluate and move only the assets that resolve to ``event``.

        Events have no hero image field of their own -- only EditorJS
        content assets -- so this is the ``_sync_entity_assets`` call alone.
        """
        if event.campaign_id is None:
            return []
        return self._sync_entity_assets(event.campaign, KIND_EVENT, event.id)

    def sync_feedback_assets(self, feedback) -> list[LifecycleEntry]:
        """Re-evaluate assets embedded in one feedback feature's content."""
        if feedback.campaign_id is None:
            return []
        return self._sync_entity_assets(
            feedback.campaign,
            KIND_FEEDBACK,
            feedback.id,
        )


def summarize(entries: list[LifecycleEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.action] = counts.get(entry.action, 0) + 1
    return counts


def report_to_json(entries: list[LifecycleEntry]) -> str:
    import json
    from dataclasses import asdict

    return json.dumps([asdict(entry) for entry in entries], indent=2, sort_keys=True)
