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
    """Metadata for an uploaded media object addressed by its storage path."""

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

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["mime"]),
        ]
