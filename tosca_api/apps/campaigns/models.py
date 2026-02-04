"""
Campaign model - Core container for organizing GeoStories, Events, and Feedback.

A Campaign represents a thematic initiative (e.g., "City Center Redesign 2025")
that groups related spatial content together.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel
from tosca_api.apps.core.sanitization import sanitize_simple


class Campaign(TimeStampedModel):
    """
    Campaign model for grouping features (stories, events, feedback) under a
    thematic initiative.
    
    Attributes:
        id: UUID primary key
        title: Campaign name (required)
        summary: Optional longer description
        status: Draft/Active/Archived lifecycle state
        visibility: Public/Private access control
        created_by: Owner/creator of the campaign
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="campaigns",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Campaign"
        verbose_name_plural = "Campaigns"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs) -> None:
        """Override save to sanitize text fields enforce Zero Trust."""
        self.title = sanitize_simple(self.title)
        self.summary = sanitize_simple(self.summary)
        super().save(*args, **kwargs)
