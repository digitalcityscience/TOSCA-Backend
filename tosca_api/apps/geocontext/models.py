"""
GeoContext model - Shared rich text content block.

GeoContext holds the content (text, rich HTML) that is linked 1:1 to
features like GeoStory, CalendarEvent, or GeoFeedback. This allows
the content to be managed independently while being tightly coupled
to its parent feature.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel


class GeoContext(TimeStampedModel):
    """
    Shared content block model.

    This model stores text/rich content that is linked 1:1 to other
    feature models (GeoStory, CalendarEvent, GeoFeedback).

    Attributes:
        id: UUID primary key
        content: The actual text content (can be plain or rich HTML)
        content_type: Whether the content is simple text or rich HTML
        created_by: The user who created this content block
    """

    class ContentType(models.TextChoices):
        SIMPLE = "simple", "Simple Text"
        RICH = "rich", "Rich HTML"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.TextField(blank=True, default="")
    content_type = models.CharField(
        max_length=20,
        choices=ContentType.choices,
        default=ContentType.SIMPLE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="geocontexts",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "GeoContext"
        verbose_name_plural = "GeoContexts"

    def __str__(self) -> str:
        # Return first 50 chars of content or a placeholder
        preview = self.content[:50] if self.content else "(empty)"
        return f"GeoContext: {preview}..."
