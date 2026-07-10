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

    Soft-delete decision (deliberately no `deleted_at`/`is_deleted`): hard
    delete is intentional here, matching every other cascading-delete model
    in this codebase (Workspace/Store/Layer also hard-delete). Adding
    soft-delete would mean filtering it out of every existing queryset
    across campaigns/events/geostories/feedback, plus admin and API
    changes, for a risk that `usage_summary()` + a warning on the delete
    confirmation page already covers: the operator sees exactly what will
    be cascade-deleted before confirming. Revisit only if a real recovery
    requirement shows up (e.g. accidental-delete reports in practice) —
    don't build it speculatively.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
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

    def usage_summary(self) -> dict[str, int]:
        """
        Return how many Event / EventSeries / GeoStory / GeoFeedback rows
        belong to this campaign.

        Unlike Layer.usage_summary() (which warns about references that
        would be *orphaned*), deleting a Campaign CASCADE-deletes these
        rows outright — the admin delete-confirmation page uses this to
        warn an operator before that cascade runs.
        """
        return {
            "events": self.events.count(),
            "event_series": self.event_series.count(),
            "geostories": self.geostories.count(),
            "feedbacks": self.feedbacks.count(),
        }
