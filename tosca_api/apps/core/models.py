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
    ownership scope so org-scoped permission/queryset chains and the future
    Garage path scheme + archive lifecycle (PR2/PR3) have something to key
    off. Both are nullable: not every asset can be traced back to a single
    Campaign (see ``backfill_media_asset_ownership`` docstring for the
    matching strategy and its limits) and orphaned uploads are a real,
    expected state -- forcing NOT NULL here would just move the "no owner"
    case into a migration failure instead of a queryable field.
    """

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

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["mime"]),
            models.Index(fields=["owner_org"]),
            models.Index(fields=["campaign"]),
        ]
