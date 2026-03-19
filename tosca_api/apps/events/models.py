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
from django.core.validators import MinValueValidator
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel
from tosca_api.apps.core.sanitization import sanitize_simple

VALID_WEEKDAYS = frozenset(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
)


class EventType(TimeStampedModel):
    """Registry describing how an event type maps to profile behavior."""

    class ProfileMode(models.TextChoices):
        CORE = "core", "Core"
        EXTENSION = "extension", "Extension"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=255)
    profile_mode = models.CharField(
        max_length=20,
        choices=ProfileMode.choices,
        default=ProfileMode.CORE,
    )
    profile_key = models.CharField(max_length=100, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["label"]
        verbose_name = "Event Type"
        verbose_name_plural = "Event Types"

    def __str__(self) -> str:
        return self.label

    def clean(self) -> None:
        errors = {}

        if self.profile_mode == self.ProfileMode.CORE and self.profile_key:
            errors["profile_key"] = "Core event types must not define a profile key."

        if self.profile_mode == self.ProfileMode.EXTENSION and not self.profile_key:
            errors["profile_key"] = "Extension event types require a profile key."

        if errors:
            raise ValidationError(errors)

        super().clean()

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


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
    """Grouping and recurrence definition model for batch and recurring events."""

    class SeriesMode(models.TextChoices):
        MANUAL_BATCH = "manual_batch", "Manual Batch"
        RECURRING = "recurring", "Recurring"

    class RecurrenceType(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    class MonthlyRuleType(models.TextChoices):
        DAY_OF_MONTH = "day_of_month", "Day of Month"
        NTH_WEEKDAY = "nth_weekday", "Nth Weekday"

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
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_event_series",
        null=True,
        blank=True,
    )
    default_context = models.ForeignKey(
        "geocontext.GeoContext",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_event_series",
    )
    series_mode = models.CharField(
        max_length=20,
        choices=SeriesMode.choices,
        default=SeriesMode.MANUAL_BATCH,
    )
    recurrence_type = models.CharField(
        max_length=20,
        choices=RecurrenceType.choices,
        blank=True,
        default="",
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    occurrence_count = models.PositiveIntegerField(null=True, blank=True)
    interval = models.PositiveIntegerField(default=1)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    timezone = models.CharField(max_length=64, blank=True, default="")
    monthly_rule_type = models.CharField(
        max_length=20,
        choices=MonthlyRuleType.choices,
        blank=True,
        default="",
    )
    day_of_month = models.PositiveSmallIntegerField(null=True, blank=True)
    week_of_month = models.PositiveSmallIntegerField(null=True, blank=True)
    weekday_of_month = models.CharField(max_length=20, blank=True, default="")
    by_weekday = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Event Series"
        verbose_name_plural = "Event Series"
        indexes = [
            models.Index(fields=["campaign", "event_type"]),
        ]

    def __str__(self) -> str:
        return self.name or str(self.id)

    def clean(self) -> None:
        errors = {}

        if self.created_by_id is None:
            errors["created_by"] = "Event series require a creator."
        if self.start_date is None:
            errors["start_date"] = "Event series require a start date."
        if self.start_time is None:
            errors["start_time"] = "Event series require a start time."
        if self.end_time is None:
            errors["end_time"] = "Event series require an end time."
        if not self.timezone:
            errors["timezone"] = "Event series require a timezone."

        if self.interval < 1:
            errors["interval"] = "Interval must be at least 1."
        if self.occurrence_count is not None and self.occurrence_count < 1:
            errors["occurrence_count"] = "Occurrence count must be at least 1."

        if self.start_time and self.end_time and self.start_date:
            if self.end_date:
                same_or_before = (self.end_date, self.end_time) <= (
                    self.start_date,
                    self.start_time,
                )
                if same_or_before:
                    errors["end_date"] = (
                        "Series end date/time must be after the start date/time."
                    )
            elif self.end_time <= self.start_time:
                errors["end_time"] = (
                    "End time must be after start time for same-day events. "
                    "Use end_date for multi-day events."
                )

        if self.series_mode == self.SeriesMode.RECURRING:
            if not self.recurrence_type:
                errors["recurrence_type"] = "Recurring series require a recurrence type."
            if bool(self.end_date) == bool(self.occurrence_count):
                errors["end_date"] = "Use either end_date or occurrence_count."
            if (
                self.start_time is not None
                and self.end_time is not None
                and self.end_time <= self.start_time
            ):
                errors["end_time"] = (
                    "Recurring generation currently requires same-day end times "
                    "after start_time."
                )

            invalid_weekdays = set(self.by_weekday) - VALID_WEEKDAYS
            if invalid_weekdays:
                errors["by_weekday"] = (
                    f"Invalid weekday values: {sorted(invalid_weekdays)}."
                )

            if self.recurrence_type == self.RecurrenceType.WEEKLY and not self.by_weekday:
                errors["by_weekday"] = "Weekly recurrence requires at least one weekday."

            if self.recurrence_type == self.RecurrenceType.MONTHLY:
                if not self.monthly_rule_type:
                    errors["monthly_rule_type"] = (
                        "Monthly recurrence requires a monthly rule type."
                    )
                elif self.monthly_rule_type == self.MonthlyRuleType.DAY_OF_MONTH:
                    if not self.day_of_month:
                        errors["day_of_month"] = (
                            "Day-of-month rule requires day_of_month."
                        )
                    elif not 1 <= self.day_of_month <= 31:
                        errors["day_of_month"] = "day_of_month must be between 1 and 31."
                elif self.monthly_rule_type == self.MonthlyRuleType.NTH_WEEKDAY:
                    if not self.week_of_month or not self.weekday_of_month:
                        errors["monthly_rule_type"] = (
                            "Nth-weekday rule requires week_of_month and weekday_of_month."
                        )
                    else:
                        if not 1 <= self.week_of_month <= 5:
                            errors["week_of_month"] = (
                                "week_of_month must be between 1 and 5."
                            )
                        if self.weekday_of_month not in VALID_WEEKDAYS:
                            errors["weekday_of_month"] = (
                                "weekday_of_month must be a valid weekday name."
                            )

        if self.series_mode == self.SeriesMode.MANUAL_BATCH:
            if self.recurrence_type:
                errors["recurrence_type"] = (
                    "Manual batches must not define a recurrence type."
                )
            if self.end_date:
                errors["end_date"] = "Manual batches must not define an end date."
            if self.occurrence_count is not None:
                errors["occurrence_count"] = (
                    "Manual batches must not define an occurrence count."
                )
            if self.by_weekday:
                errors["by_weekday"] = (
                    "Manual batches must not define recurring weekdays."
                )
            if self.monthly_rule_type or self.day_of_month or self.week_of_month or self.weekday_of_month:
                errors["monthly_rule_type"] = (
                    "Manual batches must not define monthly recurrence rules."
                )

        if errors:
            raise ValidationError(errors)

        super().clean()

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

class EventSeriesDate(TimeStampedModel):
    """Explicit occurrence date for manual batch event series."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    series = models.ForeignKey(
        EventSeries,
        on_delete=models.CASCADE,
        related_name="dates",
    )
    occurrence_date = models.DateField()
    display_order = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["display_order", "occurrence_date", "created_at"]
        verbose_name = "Event Series Date"
        verbose_name_plural = "Event Series Dates"
        constraints = [
            models.UniqueConstraint(
                fields=["series", "occurrence_date"],
                name="events_evtseriesdate_series_date_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.series} @ {self.occurrence_date}"

    def save(self, *args, **kwargs) -> None:
        if self._state.adding and self.display_order is None:
            max_order = (
                EventSeriesDate.objects.filter(series=self.series).aggregate(
                    models.Max("display_order")
                )["display_order__max"]
            )
            self.display_order = (max_order or 0) + 1
        self.full_clean()
        super().save(*args, **kwargs)


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
        verbose_name = "Event Layer"
        verbose_name_plural = "Event Layers"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "layer"],
                name="events_event_layer_uniq",
            ),
        ]

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


class BaseEventProfile(TimeStampedModel):
    """Shared validation for event extension profile tables."""

    expected_profile_key: str | None = None

    class Meta:
        abstract = True

    def clean(self) -> None:
        errors = {}

        if not self.event_id:
            errors["event"] = "Extension profiles require an event."
        else:
            event_type = self.event.event_type
            if event_type is None:
                errors["event"] = "Extension profiles require an event type."
            elif event_type.profile_mode != EventType.ProfileMode.EXTENSION:
                errors["event"] = "Core event types cannot have extension profiles."
            elif event_type.profile_key != self.expected_profile_key:
                errors["event"] = (
                    f"This profile requires event_type.profile_key="
                    f"'{self.expected_profile_key}'."
                )

        if errors:
            raise ValidationError(errors)

        super().clean()

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class PublicHealthEventProfile(BaseEventProfile):
    """Public health specific event metadata."""

    expected_profile_key = "public_health"

    event = models.OneToOneField(
        Event,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="public_health_profile",
    )
    insurance_eligible = models.BooleanField(default=False)
    referral_required = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Public Health Event Profile"
        verbose_name_plural = "Public Health Event Profiles"

    def __str__(self) -> str:
        return f"Public Health Profile: {self.event}"


class SportsEventProfile(BaseEventProfile):
    """Sports specific event metadata."""

    expected_profile_key = "sports"

    event = models.OneToOneField(
        Event,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="sports_profile",
    )
    sport_name = models.CharField(max_length=255, blank=True, default="")
    skill_level = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        verbose_name = "Sports Event Profile"
        verbose_name_plural = "Sports Event Profiles"

    def __str__(self) -> str:
        return f"Sports Profile: {self.event}"


class CultureEventProfile(BaseEventProfile):
    """Culture specific event metadata."""

    expected_profile_key = "culture"

    event = models.OneToOneField(
        Event,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="culture_profile",
    )
    format_label = models.CharField(max_length=255, blank=True, default="")
    age_rating = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        verbose_name = "Culture Event Profile"
        verbose_name_plural = "Culture Event Profiles"

    def __str__(self) -> str:
        return f"Culture Profile: {self.event}"
