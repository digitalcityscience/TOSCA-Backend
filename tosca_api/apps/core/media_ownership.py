"""
Backfill ``MediaAsset.owner_org`` / ``MediaAsset.campaign`` for existing rows.

Epic 11 PR1 (§4/§6.1 of ``docs/development/epic-11-campaign-ownership-visibility-garage-lifecycle-14082026.md``)
adds ownership fields to ``MediaAsset`` but the model previously had no link
to a Campaign at all -- only a weak, implicit tie through EditorJS content
(``GeoContext.content`` embeds ``<storage-url>`` references) or, for
GeoStory hero images, a direct FK field. This module is the read-only
matching logic that turns those implicit ties into an explicit FK, plus the
apply step.

Matching strategy (best-effort, in priority order for each unmatched asset):

1. **Hero image** -- a ``GeoStory.hero_image`` field whose stored name equals
   the asset's ``storage_path`` gets that GeoStory's campaign directly.
2. **EditorJS content reference** -- scan every ``GeoContext.content`` block
   for embedded storage paths (image blocks store the browser-facing URL,
   see ``core.editorjs._normalize_image``) and match against
   ``asset.storage_path``. Once a GeoContext is found, resolve its owning
   Campaign through the *first* of: linked GeoStory, linked Event,
   EventSeries.default_context.

Assets that don't match either path are left ``campaign=None`` --
deliberately: per §6.1 of the ticket, whether unmatched assets should stay
nullable or become a hard error is a call for later, not something this
backfill should paper over by guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from django.db.models import QuerySet


@dataclass
class BackfillEntry:
    """One asset's resolved (or unresolved) ownership, for reporting."""

    asset_id: str
    storage_path: str
    matched_via: str  # "hero_image" | "editorjs_content" | "unmatched"
    campaign_id: str | None
    organization_id: str | None


def iter_storage_paths_in_content(content: dict) -> Iterator[str]:
    """Yield every storage-relative path embedded in an Editor.js document.

    Only ``image`` blocks carry a file reference; the URL was validated and
    normalized by ``core.editorjs._normalize_image`` at save time, so it is
    always an absolute browser URL under ``MEDIA_URL`` (or an S3-style
    external URL once epic-11 storage lands) -- we match on suffix rather
    than re-deriving the storage-relative path to stay resilient to which
    backend produced the URL.
    """
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
    """True when ``url`` is plausibly a reference to ``storage_path``.

    Matches on a trailing-segment basis (URL ends with the storage path)
    since the URL may be prefixed by ``MEDIA_URL``, a signed query string,
    or a CDN host -- but must always end with the storage-relative key.
    """
    return url.rstrip("/").endswith(storage_path.lstrip("/"))


def _campaign_for_geocontext(context_id) -> "object | None":
    """Resolve the owning Campaign for a GeoContext, or ``None``.

    Checked in order: GeoStory (1:1 ``context``), Event (nullable FK
    ``context`` -- an explicit override), EventSeries
    (``default_context``, the series-level fallback events without their
    own override resolve to). First match wins; a GeoContext is expected to
    be used by at most one of these in practice, but if it is used by more
    than one, the first hit determines the asset's Campaign, this is a
    best-effort backfill, not a guaranteed-unique assignment.
    """
    from tosca_api.apps.geostories.models import GeoStory
    from tosca_api.apps.events.models import Event, EventSeries

    story = GeoStory.objects.filter(context_id=context_id).select_related("campaign").first()
    if story is not None:
        return story.campaign

    event = Event.objects.filter(context_id=context_id).select_related("campaign").first()
    if event is not None:
        return event.campaign

    series = (
        EventSeries.objects.filter(default_context_id=context_id)
        .select_related("campaign")
        .first()
    )
    if series is not None:
        return series.campaign

    return None


def plan_backfill(assets: QuerySet | None = None) -> list[BackfillEntry]:
    """Compute (without writing) the campaign/org match for every asset.

    ``assets`` defaults to every ``MediaAsset`` with no ``campaign`` yet, so
    a re-run only recomputes what's still unresolved -- already-linked rows
    (e.g. set by a normal upload flow once campaign linking exists there)
    are left untouched.
    """
    from tosca_api.apps.core.models import MediaAsset
    from tosca_api.apps.geostories.models import GeoStory
    from tosca_api.apps.geocontext.models import GeoContext

    if assets is None:
        assets = MediaAsset.objects.filter(campaign__isnull=True)

    # Hero-image index: storage name -> GeoStory. Building this once avoids
    # an N+1 query loop over every asset.
    hero_index: dict[str, GeoStory] = {
        story.hero_image.name: story
        for story in GeoStory.objects.exclude(hero_image="").select_related("campaign")
        if story.hero_image
    }

    # EditorJS content index: storage-path suffix -> GeoContext id. Built by
    # scanning every GeoContext once rather than per-asset, since the asset
    # count is typically >> GeoContext count.
    content_index: list[tuple[str, "object"]] = []
    for context in GeoContext.objects.only("id", "content").iterator():
        for url in iter_storage_paths_in_content(context.content):
            content_index.append((url, context.id))

    entries: list[BackfillEntry] = []
    for asset in assets.iterator():
        story = hero_index.get(asset.storage_path)
        if story is not None:
            entries.append(
                BackfillEntry(
                    asset_id=str(asset.id),
                    storage_path=asset.storage_path,
                    matched_via="hero_image",
                    campaign_id=str(story.campaign_id),
                    organization_id=str(story.campaign.organization_id),
                )
            )
            continue

        matched_context_id = next(
            (
                context_id
                for url, context_id in content_index
                if _url_references_storage_path(url, asset.storage_path)
            ),
            None,
        )
        if matched_context_id is not None:
            campaign = _campaign_for_geocontext(matched_context_id)
            if campaign is not None:
                entries.append(
                    BackfillEntry(
                        asset_id=str(asset.id),
                        storage_path=asset.storage_path,
                        matched_via="editorjs_content",
                        campaign_id=str(campaign.id),
                        organization_id=str(campaign.organization_id),
                    )
                )
                continue

        entries.append(
            BackfillEntry(
                asset_id=str(asset.id),
                storage_path=asset.storage_path,
                matched_via="unmatched",
                campaign_id=None,
                organization_id=None,
            )
        )

    return entries


def apply_backfill(entries: Iterable[BackfillEntry]) -> int:
    """Write resolved (``campaign_id`` not None) entries. Returns rows updated.

    Batches in chunks so a very large asset table doesn't hold one giant
    transaction (matches the batch/resumable requirement in the ticket's
    §4, even though this operates on already-computed matches rather than
    S3 copies).
    """
    from tosca_api.apps.core.models import MediaAsset

    resolved = [e for e in entries if e.campaign_id is not None]
    updated = 0
    batch_size = 500
    for start in range(0, len(resolved), batch_size):
        batch = resolved[start : start + batch_size]
        assets_by_id = {
            str(a.id): a
            for a in MediaAsset.objects.filter(id__in=[e.asset_id for e in batch])
        }
        to_update = []
        for entry in batch:
            asset = assets_by_id.get(entry.asset_id)
            if asset is None:
                continue
            asset.campaign_id = entry.campaign_id
            asset.owner_org_id = entry.organization_id
            to_update.append(asset)
        if to_update:
            MediaAsset.objects.bulk_update(to_update, ["campaign", "owner_org"])
            updated += len(to_update)
    return updated


def summarize(entries: list[BackfillEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.matched_via] = counts.get(entry.matched_via, 0) + 1
    return counts
