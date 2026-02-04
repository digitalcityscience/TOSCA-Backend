"""
GeoStory model - The core narrative unit.

A GeoStory combines a rich text narrative (GeoContext) with map layers (LayerRef)
and is organized within a Campaign.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel
from tosca_api.apps.core.sanitization import sanitize_simple


class GeoStory(TimeStampedModel):
    """
    A specific story or narrative attached to a location/map view.

    Attributes:
        id: UUID primary key
        title: Headline of the story (sanitized)
        summary: Brief intro/description (sanitized)
        status: Draft/Published/Archived
        campaign: The parent campaign this story belongs to
        author: The creator/owner
        context: 1:1 link to the rich content content block
        layers: M2M link to map layers
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.CASCADE,
        related_name="geostories",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="geostories",
    )
    context = models.OneToOneField(
        "geocontext.GeoContext",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="geostory",
    )
    layers = models.ManyToManyField(
        "layerrefs.LayerRef",
        through="GeoStoryLayer",
        related_name="geostories",
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "GeoStory"
        verbose_name_plural = "GeoStories"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs) -> None:
        """Override save to enforce Zero Trust sanitization."""
        self.title = sanitize_simple(self.title)
        self.summary = sanitize_simple(self.summary)
        super().save(*args, **kwargs)


class GeoStoryLayer(models.Model):
    """
    Through model for GeoStory <-> LayerRef.
    Allows ordering of layers within a story.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    geostory = models.ForeignKey(GeoStory, on_delete=models.CASCADE)
    layer = models.ForeignKey("layerrefs.LayerRef", on_delete=models.CASCADE)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "created_at"]
        unique_together = ("geostory", "layer")
        verbose_name = "GeoStory Layer"
        verbose_name_plural = "GeoStory Layers"

    def __str__(self) -> str:
        return f"{self.geostory} - {self.layer} ({self.display_order})"

    def save(self, *args, **kwargs) -> None:
        """
        Override save to auto-increment display_order if not specified.
        This fixes the issue where multiple layers added in Admin all get order 0.
        """
        if self._state.adding and self.display_order == 0:
            # Find the current maximum order for this story
            max_order = (
                GeoStoryLayer.objects.filter(geostory=self.geostory).aggregate(
                    models.Max("display_order")
                )["display_order__max"]
            )
            if max_order is not None:
                self.display_order = max_order + 1
        super().save(*args, **kwargs)
