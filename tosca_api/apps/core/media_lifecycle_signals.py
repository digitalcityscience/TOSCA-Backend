"""
Campaign status/visibility -> media archive lifecycle sync (epic-11 PR3).

Fires on ``Campaign.save()`` post_save, but only performs the (potentially
expensive, storage-touching) asset sweep when a field the lifecycle actually
depends on -- ``status`` or ``visibility`` -- changed. An unrelated Campaign
edit (e.g. ``title``) must not trigger an object-storage scan.

Mirrors the ``geodata_providers.signals`` pre_save/post_save prior-state
capture pattern. Wired up in ``CoreConfig.ready()`` (this signal lives in
``core`` rather than ``campaigns`` because the lifecycle service and
``MediaAsset`` are both core-owned, and core already depends on campaigns --
the reverse dependency direction would create an import cycle).
"""

from __future__ import annotations

import logging

from django.core.files.storage import storages
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geostories.models import GeoStory

from .media_lifecycle import MediaLifecycleService, summarize

logger = logging.getLogger(__name__)

_PRIOR_STATE_ATTR = "_media_lifecycle_prior_state"


def _lifecycle_service() -> MediaLifecycleService:
    return MediaLifecycleService(storage_for_alias=lambda alias: storages[alias])


@receiver(pre_save, sender=Campaign)
def _capture_campaign_prior_state(sender, instance, **kwargs):
    if instance.pk is None:
        setattr(instance, _PRIOR_STATE_ATTR, None)
        return
    try:
        prior = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        setattr(instance, _PRIOR_STATE_ATTR, None)
    else:
        setattr(instance, _PRIOR_STATE_ATTR, (prior.status, prior.visibility))


@receiver(post_save, sender=Campaign)
def _sync_campaign_media_lifecycle(sender, instance, created, **kwargs):
    if created:
        return  # a brand-new campaign has no assets yet
    prior_state = getattr(instance, _PRIOR_STATE_ATTR, None)
    current_state = (instance.status, instance.visibility)
    if prior_state == current_state:
        return

    entries = _lifecycle_service().sync_campaign_assets(instance)
    failures = [e for e in entries if e.status != "ok"]
    if entries:
        logger.info(
            "Campaign %s lifecycle sync (%r -> %r): %s%s",
            instance.pk,
            prior_state,
            current_state,
            summarize(entries),
            f" -- {len(failures)} failure(s)" if failures else "",
        )


@receiver(pre_save, sender=GeoStory)
def _capture_geostory_prior_status(sender, instance, **kwargs):
    if instance.pk is None:
        setattr(instance, _PRIOR_STATE_ATTR, None)
        return
    try:
        prior = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        setattr(instance, _PRIOR_STATE_ATTR, None)
    else:
        setattr(instance, _PRIOR_STATE_ATTR, prior.status)


@receiver(post_save, sender=GeoStory)
def _sync_geostory_media_lifecycle(sender, instance, created, **kwargs):
    if created:
        return  # a brand-new story has no linked assets yet
    prior_status = getattr(instance, _PRIOR_STATE_ATTR, None)
    if prior_status == instance.status:
        return

    entries = _lifecycle_service().sync_story_assets(instance)
    failures = [e for e in entries if e.status != "ok"]
    if entries:
        logger.info(
            "GeoStory %s lifecycle sync (%r -> %r): %s%s",
            instance.pk,
            prior_status,
            instance.status,
            summarize(entries),
            f" -- {len(failures)} failure(s)" if failures else "",
        )
