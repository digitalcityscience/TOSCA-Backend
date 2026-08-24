"""Claim and place Editor.js uploads when feature content is saved."""

from django.core.files.storage import storages
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from tosca_api.apps.events.models import Event, EventSeries
from tosca_api.apps.feedback.models import GeoFeedback
from tosca_api.apps.geostories.models import GeoStory

from .media_lifecycle import MediaLifecycleService, desired_alias_for_asset
from .media_ownership import claim_assets_referenced_by_content

_PRIOR_CONTENT_ATTR = "_content_asset_prior_content"


def _claim_after_commit(*, content, campaign) -> None:
    def reconcile():
        assets = claim_assets_referenced_by_content(content=content, campaign=campaign)
        if not assets:
            return
        service = MediaLifecycleService(storage_for_alias=lambda alias: storages[alias])
        for asset in assets:
            target_alias = desired_alias_for_asset(asset)
            if target_alias is not None:
                service.move_one(asset, target_alias)

    transaction.on_commit(reconcile)


def _capture_prior_content(sender, instance, field_name, **kwargs):
    if instance.pk is None:
        setattr(instance, _PRIOR_CONTENT_ATTR, None)
        return
    try:
        prior = sender._default_manager.only(field_name).get(pk=instance.pk)
    except sender.DoesNotExist:
        setattr(instance, _PRIOR_CONTENT_ATTR, None)
    else:
        setattr(instance, _PRIOR_CONTENT_ATTR, getattr(prior, field_name))


def _content_unchanged(instance, created, current_content) -> bool:
    if created:
        return False
    prior_content = getattr(instance, _PRIOR_CONTENT_ATTR, None)
    return prior_content == current_content


@receiver(pre_save, sender=GeoStory)
def _capture_story_prior_content(sender, instance, **kwargs):
    _capture_prior_content(sender, instance, "content")


@receiver(post_save, sender=GeoStory)
def _claim_story_content_assets(sender, instance, created, **kwargs):
    if _content_unchanged(instance, created, instance.content):
        return
    _claim_after_commit(content=instance.content, campaign=instance.campaign)


@receiver(pre_save, sender=Event)
def _capture_event_prior_content(sender, instance, **kwargs):
    _capture_prior_content(sender, instance, "content_override")


@receiver(post_save, sender=Event)
def _claim_event_content_assets(sender, instance, created, **kwargs):
    if instance.content_override is None:
        return
    if _content_unchanged(instance, created, instance.content_override):
        return
    _claim_after_commit(content=instance.content_override, campaign=instance.campaign)


@receiver(pre_save, sender=EventSeries)
def _capture_series_prior_content(sender, instance, **kwargs):
    _capture_prior_content(sender, instance, "default_content")


@receiver(post_save, sender=EventSeries)
def _claim_series_content_assets(sender, instance, created, **kwargs):
    if _content_unchanged(instance, created, instance.default_content):
        return
    _claim_after_commit(content=instance.default_content, campaign=instance.campaign)


@receiver(pre_save, sender=GeoFeedback)
def _capture_feedback_prior_content(sender, instance, **kwargs):
    _capture_prior_content(sender, instance, "content")


@receiver(post_save, sender=GeoFeedback)
def _claim_feedback_content_assets(sender, instance, created, **kwargs):
    if _content_unchanged(instance, created, instance.content):
        return
    _claim_after_commit(content=instance.content, campaign=instance.campaign)
