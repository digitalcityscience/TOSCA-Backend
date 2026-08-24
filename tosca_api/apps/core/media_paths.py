"""
Canonical Garage storage path scheme (epic-11 PR2, §4 of
``docs/development/epic-11-campaign-ownership-visibility-garage-lifecycle-14082026.md``).

The legacy path shapes are flat and inconsistent (``geostories/{id}/hero/...``,
``geocontext/editorjs/...``) and carry no ownership information in the key
itself. The canonical scheme encodes ownership directly in the path so the
archive lifecycle (PR3) can move/copy an org's or campaign's assets between
buckets by prefix alone, without a database join per object:

    orgs/<org-slug>/campaigns/<campaign-id>/stories/<story-id>/<filename>
    orgs/<org-slug>/campaigns/<campaign-id>/events/<event-id>/<filename>
    orgs/<org-slug>/campaigns/<campaign-id>/feedback/<feedback-id>/<filename>
    orgs/<org-slug>/campaigns/<campaign-id>/misc/<filename>

``misc/`` is the fallback for assets that resolve to a Campaign (via PR1's
``MediaAsset.campaign``) but not to one specific feature -- e.g. an
EditorJS image embedded in an ``EventSeries.default_content`` rather than a
single Event. Assets with no resolvable campaign (``MediaAsset.campaign is
None``, a real and expected state per PR1 §6.1) have no canonical path at
all: they are left at their current location, unassigned/orphan uploads have
no ownership scope to key a path off.

This module only *computes* paths -- see ``media_path_migration`` for the
backfill-all script that actually moves objects and rewrites
``MediaAsset.storage_path``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

# Kinds of owning entity a canonical path can be scoped to, in resolution
# priority order (checked top to bottom by ``resolve_entity``).
KIND_STORY = "story"
KIND_EVENT = "event"
KIND_FEEDBACK = "feedback"
KIND_MISC = "misc"

_ENTITY_SEGMENT = {
    KIND_STORY: "stories",
    KIND_EVENT: "events",
    KIND_FEEDBACK: "feedback",
    KIND_MISC: "misc",
}


@dataclass
class ResolvedEntity:
    """The owning Campaign plus (when known) the specific story/event scope."""

    org_slug: str
    campaign_id: str
    kind: str  # KIND_STORY | KIND_EVENT | KIND_FEEDBACK | KIND_MISC
    entity_id: str | None  # feature id, or None for KIND_MISC


def canonical_storage_path(resolved: ResolvedEntity, filename: str) -> str:
    """Build the canonical key for a resolved entity + filename.

    ``filename`` should already be the final path segment (e.g.
    ``<uuid>.png``), not a full legacy path -- callers extract it from the
    asset's current ``storage_path`` before calling this.
    """
    segment = _ENTITY_SEGMENT[resolved.kind]
    base = f"orgs/{resolved.org_slug}/campaigns/{resolved.campaign_id}/{segment}"
    if resolved.kind == KIND_MISC:
        return f"{base}/{filename}"
    return f"{base}/{resolved.entity_id}/{filename}"


def filename_from_legacy_path(storage_path: str) -> str:
    """Extract the trailing filename segment from any legacy storage path."""
    return storage_path.rsplit("/", 1)[-1]


def _iter_storage_paths_in_content(content: dict) -> Iterator[str]:
    # Local re-implementation (not imported from media_ownership) to avoid a
    # cross-module dependency for what is a two-line block scan; both copies
    # are intentionally kept in lockstep with core.editorjs._normalize_image.
    blocks = (content or {}).get("blocks") or []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        data = block.get("data") or {}
        file_in = data.get("file") or {}
        url = file_in.get("url")
        if isinstance(url, str) and url:
            yield url


def _url_references_storage_path(url: str, storage_path: str) -> bool:
    return url.rstrip("/").endswith(storage_path.lstrip("/"))


def resolve_entity(asset) -> ResolvedEntity | None:
    """Resolve the canonical-path scope for a ``MediaAsset``.

    Returns ``None`` when the asset has no ``campaign`` (unassigned/orphan --
    per PR1 §6.1, a real and expected state; there is nothing to key a
    canonical path off). Otherwise resolves, in priority order:

    1. **Hero image** -- a ``GeoStory.hero_image`` matching the asset's
       current ``storage_path`` exactly -> ``KIND_STORY``.
    2. **EditorJS content reference** -- the asset's path is embedded directly
       in a story, event override, or feedback feature.
    3. **Campaign fallback** -- campaign is known but no single entity is ->
       ``KIND_MISC``.
    """
    if asset.campaign_id is None:
        return None

    from tosca_api.apps.geostories.models import GeoStory
    from tosca_api.apps.events.models import Event
    from tosca_api.apps.feedback.models import GeoFeedback

    org_slug = asset.campaign.organization.slug
    campaign_id = str(asset.campaign_id)

    story = GeoStory.objects.filter(hero_image=asset.storage_path).first()
    if story is not None:
        return ResolvedEntity(org_slug, campaign_id, KIND_STORY, str(story.id))

    owned_sources = (
        (
            KIND_STORY,
            GeoStory.objects.filter(campaign_id=asset.campaign_id).only("id", "content"),
            "content",
        ),
        (
            KIND_EVENT,
            Event.objects.filter(
                campaign_id=asset.campaign_id,
                content_override__isnull=False,
            ).only("id", "content_override"),
            "content_override",
        ),
        (
            KIND_FEEDBACK,
            GeoFeedback.objects.filter(campaign_id=asset.campaign_id).only("id", "content"),
            "content",
        ),
    )
    for kind, queryset, field_name in owned_sources:
        for feature in queryset.iterator():
            if any(
                _url_references_storage_path(url, asset.storage_path)
                for url in _iter_storage_paths_in_content(getattr(feature, field_name))
            ):
                return ResolvedEntity(org_slug, campaign_id, kind, str(feature.id))

    return ResolvedEntity(org_slug, campaign_id, KIND_MISC, None)
