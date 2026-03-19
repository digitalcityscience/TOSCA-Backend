"""
Event model - Time-bound spatial events.

Events represent scheduled activities (workshops, discussions, meetings)
that occur at a specific time and optionally at a specific location. They belong
to a Campaign and can have associated map layers and rich content (GeoContext).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.gis.db import models as gis_models
from django.core.exceptions import ValidationError
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel
from tosca_api.apps.core.sanitization import sanitize_simple


class EventType(TimeStampedModel):
    """
    Minimal placeholder registry model for event-type assignment.

    The full registry contract lands in Task 2B.7. This placeholder exists so
    Event can carry the requested foreign key without collapsing into a UUID
    shim that would be harder to migrate later.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=255)

    class Meta:
        ordering = ["label"]
        verbose_name = "Event Type"
        verbose_name_plural = "Event Types"

    def __str__(self) -> str:
        return self.label


class TaxonomyDimension(TimeStampedModel):
    """Top-level taxonomy dimension for event classification."""

    class SelectionMode(models.TextChoices):
        SINGLE = "single", "Single"
        MULTIPLE = "multiple", "Multiple"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    selection_mode = models.CharField(
        max_length=20,
        choices=SelectionMode.choices,
        default=SelectionMode.MULTIPLE,
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "label"]
        verbose_name = "Taxonomy Dimension"
        verbose_name_plural = "Taxonomy Dimensions"

    def __str__(self) -> str:
        return self.label


class TaxonomyTerm(TimeStampedModel):
    """Term within a taxonomy dimension, with optional parent nesting."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dimension = models.ForeignKey(
        TaxonomyDimension,
        on_delete=models.CASCADE,
        related_name="terms",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    code = models.CharField(max_length=100)
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["dimension_id", "sort_order", "label"]
        verbose_name = "Taxonomy Term"
        verbose_name_plural = "Taxonomy Terms"
        constraints = [
            models.UniqueConstraint(
                fields=["dimension", "code"],
                name="events_tax_term_dim_code_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dimension}: {self.label}"

    def clean(self) -> None:
        errors = {}

        if self.parent_id:
            if self.parent_id == self.id:
                errors["parent"] = "A taxonomy term cannot be its own parent."
            elif self.dimension_id and self.parent.dimension_id != self.dimension_id:
                errors["parent"] = "Parent term must belong to the same dimension."

        if errors:
            raise ValidationError(errors)

        super().clean()

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class EventSeries(TimeStampedModel):
    """
    Minimal grouping model for recurring/batch event linkage.

    Campaign and event_type live here already because series-linked event
    invariants depend on them in Task 2B.4. Recurrence detail remains deferred
    to the later EventSeries schema task.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.CASCADE,
        related_name="event_series",
        null=True,
        blank=True,
    )
    event_type = models.ForeignKey(
        EventType,
        on_delete=models.PROTECT,
        related_name="series",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255, blank=True, default="")
    default_context = models.ForeignKey(
        "geocontext.GeoContext",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_event_series",
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Event Series"
        verbose_name_plural = "Event Series"
        indexes = [
            models.Index(fields=["campaign", "event_type"]),
        ]

    def __str__(self) -> str:
        return self.name or str(self.id)


class Event(TimeStampedModel):
    """
    An event with physical, online, or hybrid delivery modes.

    Attributes:
        id: UUID primary key
        campaign: Parent campaign
        title: Event title (sanitized)
        description: Brief description (sanitized)
        context: Optional per-event context override
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

    class LocationMode(models.TextChoices):
        PHYSICAL = "physical", "Physical"
        ONLINE = "online", "Online"
        HYBRID = "hybrid", "Hybrid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.ForeignKey(
        EventType,
        on_delete=models.PROTECT,
        related_name="events",
        null=True,
        blank=True,
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    context = models.ForeignKey(
        "geocontext.GeoContext",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )

    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()

    location_mode = models.CharField(
        max_length=20,
        choices=LocationMode.choices,
        default=LocationMode.PHYSICAL,
    )

    # Spatial location (optional) - WGS84
    location = gis_models.PointField(srid=4326, blank=True, null=True)
    online_url = models.URLField(blank=True, default="")
    online_platform = models.CharField(max_length=255, blank=True, default="")
    access_notes = models.TextField(blank=True, default="")
    provider_name = models.CharField(max_length=255, blank=True, default="")
    provider_url = models.URLField(blank=True, default="")
    provider_contact = models.TextField(blank=True, default="")
    series = models.ForeignKey(
        EventSeries,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    occurrence_index = models.PositiveIntegerField(null=True, blank=True)
    is_exception = models.BooleanField(default=False)
    original_start_datetime = models.DateTimeField(null=True, blank=True)

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

    # Reverse generic relations for cascading deletes of FeatureLinks
    feature_links_source = GenericRelation(
        "featurelinks.FeatureLink",
        content_type_field="source_content_type",
        object_id_field="source_object_id",
        related_query_name="event_source",
    )
    feature_links_target = GenericRelation(
        "featurelinks.FeatureLink",
        content_type_field="target_content_type",
        object_id_field="target_object_id",
        related_query_name="event_target",
    )

    class Meta:
        ordering = ["start_datetime"]
        verbose_name = "Event"
        verbose_name_plural = "Events"
        indexes = [
            models.Index(fields=["campaign"]),
            models.Index(fields=["start_datetime", "end_datetime"]),
            models.Index(fields=["status"]),
            models.Index(
                fields=["campaign", "status", "start_datetime"],
                name="events_evt_cmp_stat_start_idx",
            ),
            models.Index(
                fields=["event_type", "start_datetime"],
                name="events_evt_type_start_idx",
            ),
            models.Index(
                fields=["location_mode", "start_datetime"],
                name="events_evt_locmode_start_idx",
            ),
            models.Index(
                fields=["series", "start_datetime"],
                name="events_evt_series_start_idx",
            ),
        ]
        constraints = [
            # Ensure end_datetime >= start_datetime
            models.CheckConstraint(
                condition=models.Q(end_datetime__gte=models.F("start_datetime")),
                name="event_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["series", "occurrence_index"],
                condition=models.Q(series__isnull=False),
                name="events_evt_ser_occ_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.start_datetime.date()})"

    @property
    def effective_context(self):
        """
        Resolve content precedence for an event occurrence.

        Resolution order:
        1. Event.context override
        2. EventSeries.default_context
        3. None
        """
        if self.context_id:
            return self.context
        if self.series_id and self.series and self.series.default_context_id:
            return self.series.default_context
        return None

    def clean(self) -> None:
        """Validate the event."""
        errors = {}
        has_online_access = bool(self.online_url or self.online_platform)

        # Validate end >= start at application level too
        if self.start_datetime and self.end_datetime:
            if self.end_datetime < self.start_datetime:
                errors["end_datetime"] = "End datetime must be after start datetime."

        if self.location_mode == self.LocationMode.PHYSICAL and self.location is None:
            errors["location"] = "Physical events require geometry."

        if self.location_mode == self.LocationMode.ONLINE:
            if not has_online_access:
                errors["online_url"] = "Online events require an online URL or platform."
            if self.location is not None:
                errors["location"] = "Online events cannot include geometry."

        if self.location_mode == self.LocationMode.HYBRID:
            if self.location is None:
                errors["location"] = "Hybrid events require geometry."
            if not has_online_access:
                errors["online_url"] = "Hybrid events require an online URL or platform."

        if self.is_exception and not self.series_id:
            errors["is_exception"] = "Only series events can be marked as exceptions."

        if self.series_id and self.occurrence_index is None:
            errors["occurrence_index"] = "Series events require an occurrence index."

        if self.series_id:
            if self.series.campaign_id is None:
                errors["series"] = "Series-linked events require a series campaign."
            elif self.series.campaign_id != self.campaign_id:
                errors["campaign"] = "Series-linked events must match the series campaign."

            if self.series.event_type_id is None:
                errors["series"] = "Series-linked events require a series event type."
            elif self.event_type_id is None:
                errors["event_type"] = "Series-linked events require an event type."
            elif self.series.event_type_id != self.event_type_id:
                errors["event_type"] = "Series-linked events must match the series event type."

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
    Through model for Event <-> LayerRef.
    Allows ordering of layers within an event.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
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


class EventTerm(TimeStampedModel):
    """Assignment table linking events to taxonomy terms."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="event_terms",
    )
    term = models.ForeignKey(
        TaxonomyTerm,
        on_delete=models.CASCADE,
        related_name="event_terms",
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Event Term"
        verbose_name_plural = "Event Terms"
        indexes = [
            models.Index(fields=["term", "event"], name="events_evtterm_term_evt_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "term"],
                name="events_event_term_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event} -> {self.term}"

    def clean(self) -> None:
        errors = {}

        if self.event_id and self.term_id:
            dimension = self.term.dimension
            if dimension.selection_mode == TaxonomyDimension.SelectionMode.SINGLE:
                conflicting_terms = EventTerm.objects.filter(
                    event_id=self.event_id,
                    term__dimension_id=self.term.dimension_id,
                ).exclude(pk=self.pk)
                if conflicting_terms.exclude(term_id=self.term_id).exists():
                    errors["term"] = (
                        "Single-select dimensions allow only one term per event."
                    )

        if errors:
            raise ValidationError(errors)

        super().clean()

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)
