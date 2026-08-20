from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base model providing created and updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class MediaAsset(TimeStampedModel):
    """Metadata for an uploaded media object addressed by its storage path.

    ``owner_org``/``campaign`` (epic-11 PR1, §3.3/§6.1) give MediaAsset an
    ownership scope so org-scoped permission/queryset chains and the
    Garage path scheme + archive lifecycle (PR2/PR3) have something to key
    off. Both are nullable: not every asset can be traced back to a single
    Campaign (see ``backfill_media_asset_ownership`` docstring for the
    matching strategy and its limits) and orphaned uploads are a real,
    expected state -- forcing NOT NULL here would just move the "no owner"
    case into a migration failure instead of a queryable field.

    ``storage_alias`` (epic-11 PR3) tracks which Django storage alias
    (``default`` / ``media_public`` / ``media_archive``) currently holds the
    object. This can't be derived from ``storage_path`` alone: the PR2
    canonical path scheme (``orgs/<org>/campaigns/<id>/...``) encodes
    *ownership*, not *bucket* -- two assets can share the same canonical key
    shape while living in different buckets. The archive/restore lifecycle
    (``core.media_lifecycle``) reads and updates this field when it moves an
    object between buckets in response to a Campaign/GeoStory status or
    visibility change.
    """

    class StorageAlias(models.TextChoices):
        DEFAULT = "default", "Private (default)"
        PUBLIC = "media_public", "Public"
        ARCHIVE = "media_archive", "Archive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    storage_path = models.CharField(max_length=1024, unique=True)
    original_name = models.CharField(max_length=255, blank=True)
    mime = models.CharField(max_length=100)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size = models.PositiveBigIntegerField()
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="media_assets",
    )
    owner_org = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="media_assets",
        help_text="Owning organization, derived from the campaign this asset "
        "is used in (nullable: orphan/unlinked uploads have no owner).",
    )
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="media_assets",
        help_text="Campaign this asset is used in, when known.",
    )
    storage_alias = models.CharField(
        max_length=20,
        choices=StorageAlias.choices,
        default=StorageAlias.DEFAULT,
        help_text="Which storage alias (bucket) currently holds this object. "
        "Updated by core.media_lifecycle when a Campaign/GeoStory "
        "archive/restore transition moves the object between buckets.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["mime"]),
            models.Index(fields=["owner_org"]),
            models.Index(fields=["campaign"]),
        ]
