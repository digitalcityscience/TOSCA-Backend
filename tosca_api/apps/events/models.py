"""
CalendarEvent model - Time-bound spatial events.

CalendarEvents represent scheduled activities (workshops, discussions, meetings)
that occur at a specific time and optionally at a specific location. They belong
to a Campaign and can have associated map layers and rich content (GeoContext).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.core.exceptions import ValidationError
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel
from tosca_api.apps.core.sanitization import sanitize_simple


class CalendarEvent(TimeStampedModel):
    """
    A calendar event with optional spatial location.

    Attributes:
        id: UUID primary key
        campaign: Parent campaign
        title: Event title (sanitized)
        description: Brief description (sanitized)
        context: 1:1 link to rich content block (optional)
        start_datetime: When the event starts
        end_datetime: When the event ends
        location: Optional point location (SRID 4326)
        organizer: User who created/organizes the event
        layers: M2M link to map layers
        status: Draft/Published/Cancelled
        visibility: Public/Private
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CANCELLED = "cancelled", "Cancelled"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.CASCADE,
        related_name="events",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    context = models.OneToOneField(
        "geocontext.GeoContext",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="event",
    )

    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()

    # Spatial location (optional) - WGS84
    location = gis_models.PointField(srid=4326, blank=True, null=True)

    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="organized_events",
    )

    layers = models.ManyToManyField(
        "layerrefs.LayerRef",
        through="EventLayer",
        related_name="events",
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
    )

    class Meta:
        ordering = ["start_datetime"]
        verbose_name = "Calendar Event"
        verbose_name_plural = "Calendar Events"
        indexes = [
            models.Index(fields=["campaign"]),
            models.Index(fields=["start_datetime", "end_datetime"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            # Ensure end_datetime >= start_datetime
            models.CheckConstraint(
                condition=models.Q(end_datetime__gte=models.F("start_datetime")),
                name="event_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.start_datetime.date()})"

    def clean(self) -> None:
        """Validate the event."""
        errors = {}

        # Validate end >= start at application level too
        if self.start_datetime and self.end_datetime:
            if self.end_datetime < self.start_datetime:
                errors["end_datetime"] = "End datetime must be after start datetime."

        if errors:
            raise ValidationError(errors)

        super().clean()

    def save(self, *args, **kwargs) -> None:
        """Override save to sanitize inputs and validate."""
        self.title = sanitize_simple(self.title)
        self.description = sanitize_simple(self.description)
        self.full_clean()
        super().save(*args, **kwargs)


class EventLayer(models.Model):
    """
    Through model for CalendarEvent <-> LayerRef.
    Allows ordering of layers within an event.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(CalendarEvent, on_delete=models.CASCADE)
    layer = models.ForeignKey("layerrefs.LayerRef", on_delete=models.CASCADE)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "created_at"]
        unique_together = ("event", "layer")
        verbose_name = "Event Layer"
        verbose_name_plural = "Event Layers"

    def __str__(self) -> str:
        return f"{self.event} - {self.layer} ({self.display_order})"

    def save(self, *args, **kwargs) -> None:
        """Auto-increment display_order if not specified."""
        if self._state.adding and self.display_order == 0:
            max_order = (
                EventLayer.objects.filter(event=self.event).aggregate(
                    models.Max("display_order")
                )["display_order__max"]
            )
            if max_order is not None:
                self.display_order = max_order + 1
        super().save(*args, **kwargs)
