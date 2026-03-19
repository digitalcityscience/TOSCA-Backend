from collections import Counter
from zoneinfo import ZoneInfoNotFoundError

from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.db import transaction
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from tosca_api.apps.geocontext.models import GeoContext

from .models import (
    Event,
    EventLayer,
    EventSeries,
    EventSeriesDate,
    EventTerm,
    TaxonomyDimension,
    TaxonomyTerm,
    EventType,
    VALID_WEEKDAYS,
)
from .services import (
    KEEP_EXISTING_TERMS,
    build_occurrence_specs,
    create_occurrence_events,
    serialize_occurrence_events,
    sync_occurrence_events,
)

EVENT_SERIES_FIELDS = {
    "campaign",
    "event_type",
    "name",
    "default_context",
    "series_mode",
    "recurrence_type",
    "start_date",
    "end_date",
    "occurrence_count",
    "interval",
    "start_time",
    "end_time",
    "timezone",
    "monthly_rule_type",
    "day_of_month",
    "week_of_month",
    "weekday_of_month",
    "by_weekday",
    "notes",
}

EVENT_TEMPLATE_FIELDS = {
    "title",
    "description",
    "location_mode",
    "location",
    "online_url",
    "online_platform",
    "access_notes",
    "provider_name",
    "provider_url",
    "provider_contact",
    "status",
    "visibility",
    "context",
}

EXCEPTION_TRIGGER_FIELDS = {
    "title",
    "description",
    "start_datetime",
    "end_datetime",
    "location_mode",
    "location",
    "online_url",
    "online_platform",
    "access_notes",
    "provider_name",
    "provider_url",
    "provider_contact",
    "status",
    "visibility",
    "context",
}


# =============================================================================
# Nested Serializers
# =============================================================================


class EventGeoContextSerializer(serializers.ModelSerializer):
    """Nested serializer for event's GeoContext."""

    class Meta:
        model = GeoContext
        fields = ["id", "content", "content_type"]
        read_only_fields = fields


class EventLayerSerializer(serializers.ModelSerializer):
    """Serializer for EventLayer through model."""

    id = serializers.UUIDField(source="layer.id", read_only=True)
    layer_name = serializers.CharField(source="layer.layer_name", read_only=True)

    class Meta:
        model = EventLayer
        fields = ["id", "layer_name", "display_order"]
        read_only_fields = fields


# =============================================================================
# Event Serializers
# =============================================================================


class EventListSerializer(serializers.ModelSerializer):
    """
    Slim serializer for calendar view (list).
    Used when no spatial filtering is applied.
    """

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "status",
            "visibility",
            "created_at",
        ]
        read_only_fields = fields


class EventDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for event detail view.
    Includes nested context and layers.
    """

    context = serializers.SerializerMethodField()
    layers = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "location",
            "online_url",
            "online_platform",
            "access_notes",
            "provider_name",
            "provider_url",
            "provider_contact",
            "series",
            "occurrence_index",
            "is_exception",
            "original_start_datetime",
            "status",
            "visibility",
            "organizer",
            "context",
            "layers",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_layers(self, obj) -> list:
        """Return layers ordered by display_order."""
        through_qs = EventLayer.objects.filter(event=obj).select_related("layer")
        return EventLayerSerializer(through_qs, many=True).data

    def get_context(self, obj):
        """Return the resolved event context."""
        context = obj.effective_context
        if context is None:
            return None
        return EventGeoContextSerializer(context).data


class EventGeoSerializer(GeoFeatureModelSerializer):
    """
    GeoJSON serializer for map view.
    Returns events as GeoJSON FeatureCollection.
    """

    class Meta:
        model = Event
        geo_field = "location"
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "status",
            "visibility",
        ]
        read_only_fields = fields


class EventMapOnlineSerializer(serializers.ModelSerializer):
    """Serializer for online events returned in the dedicated map endpoint."""

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "online_url",
            "online_platform",
            "status",
            "visibility",
        ]
        read_only_fields = fields


class TaxonomyTermResolutionMixin:
    """Shared taxonomy term validation and replacement helpers."""

    def _resolve_taxonomy_terms(self, taxonomy_term_ids):
        duplicate_ids = [
            term_id
            for term_id, count in Counter(taxonomy_term_ids).items()
            if count > 1
        ]
        if duplicate_ids:
            raise serializers.ValidationError(
                {"taxonomy_term_ids": "Duplicate taxonomy term IDs are not allowed."}
            )

        terms = list(
            TaxonomyTerm.objects.select_related("dimension").filter(
                id__in=taxonomy_term_ids
            )
        )
        if len(terms) != len(taxonomy_term_ids):
            found_ids = {term.id for term in terms}
            missing_ids = [
                str(term_id)
                for term_id in taxonomy_term_ids
                if term_id not in found_ids
            ]
            raise serializers.ValidationError(
                {
                    "taxonomy_term_ids": (
                        f"Unknown taxonomy term IDs: {', '.join(missing_ids)}"
                    )
                }
            )

        single_select_counts = Counter(
            term.dimension_id
            for term in terms
            if term.dimension.selection_mode == TaxonomyDimension.SelectionMode.SINGLE
        )
        if any(count > 1 for count in single_select_counts.values()):
            raise serializers.ValidationError(
                {
                    "taxonomy_term_ids": (
                        "Single-select dimensions allow only one term per event."
                    )
                }
            )

        return terms

    def _replace_event_terms(self, event, taxonomy_terms):
        with transaction.atomic():
            EventTerm.objects.filter(event=event).delete()
            for term in taxonomy_terms:
                EventTerm.objects.create(event=event, term=term)


class EventWriteSerializer(TaxonomyTermResolutionMixin, serializers.ModelSerializer):
    """Serializer for creating/updating events."""

    taxonomy_term_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "location",
            "online_url",
            "online_platform",
            "access_notes",
            "provider_name",
            "provider_url",
            "provider_contact",
            "series",
            "occurrence_index",
            "is_exception",
            "original_start_datetime",
            "status",
            "visibility",
            "organizer",
            "context",
            "taxonomy_term_ids",
        ]
        read_only_fields = ["id", "organizer"]

    def validate(self, attrs):
        """Invoke model clean() to ensure DB constraints surface as API 400s."""
        taxonomy_term_ids = attrs.pop("taxonomy_term_ids", serializers.empty)
        if taxonomy_term_ids is not serializers.empty:
            attrs["_taxonomy_terms"] = self._resolve_taxonomy_terms(taxonomy_term_ids)

        instance = Event()
        if self.instance is not None:
            for field in self.Meta.fields:
                if field in {"id", "taxonomy_term_ids"}:
                    continue
                setattr(instance, field, getattr(self.instance, field))
            instance.pk = self.instance.pk

        for attr, value in attrs.items():
            if attr.startswith("_"):
                continue
            setattr(instance, attr, value)

        # Event.clean() enforces start_datetime <= end_datetime
        instance.clean()
        return attrs

    def create(self, validated_data):
        taxonomy_terms = validated_data.pop("_taxonomy_terms", serializers.empty)
        event = super().create(validated_data)
        if taxonomy_terms is not serializers.empty:
            self._replace_event_terms(event, taxonomy_terms)
        return event

    def update(self, instance, validated_data):
        taxonomy_terms = validated_data.pop("_taxonomy_terms", serializers.empty)
        should_mark_exception = self._should_mark_exception(
            instance=instance,
            validated_data=validated_data,
            taxonomy_terms=taxonomy_terms,
        )
        if should_mark_exception:
            validated_data["is_exception"] = True
            if (
                ("start_datetime" in validated_data or "end_datetime" in validated_data)
                and instance.original_start_datetime is None
            ):
                validated_data["original_start_datetime"] = instance.start_datetime

        event = super().update(instance, validated_data)
        if taxonomy_terms is not serializers.empty:
            self._replace_event_terms(event, taxonomy_terms)
        return event

    def _should_mark_exception(self, instance, validated_data, taxonomy_terms) -> bool:
        if not instance.series_id:
            return False

        for field in EXCEPTION_TRIGGER_FIELDS:
            if field in validated_data and validated_data[field] != getattr(instance, field):
                return True

        if taxonomy_terms is not serializers.empty:
            current_term_ids = set(
                EventTerm.objects.filter(event=instance).values_list("term_id", flat=True)
            )
            incoming_term_ids = {term.id for term in taxonomy_terms}
            if current_term_ids != incoming_term_ids:
                return True

        return False


class EventSeriesOccurrenceSerializer(serializers.Serializer):
    """Occurrence payload used by preview and write responses."""

    id = serializers.UUIDField(required=False)
    occurrence_index = serializers.IntegerField()
    occurrence_date = serializers.DateField(required=False)
    is_exception = serializers.BooleanField(required=False)
    start_datetime = serializers.DateTimeField()
    end_datetime = serializers.DateTimeField()
    original_start_datetime = serializers.DateTimeField()
    title = serializers.CharField(required=False)


class EventSeriesResponseSerializer(serializers.ModelSerializer):
    """Response serializer for event-series create and update operations."""

    occurrences = serializers.SerializerMethodField()

    class Meta:
        model = EventSeries
        fields = [
            "id",
            "campaign",
            "event_type",
            "name",
            "series_mode",
            "recurrence_type",
            "start_date",
            "end_date",
            "occurrence_count",
            "interval",
            "start_time",
            "end_time",
            "timezone",
            "occurrences",
        ]
        read_only_fields = fields

    def get_occurrences(self, obj):
        occurrences = getattr(obj, "_response_occurrences", [])
        return EventSeriesOccurrenceSerializer(occurrences, many=True).data


class EventSeriesWriteSerializer(TaxonomyTermResolutionMixin, serializers.Serializer):
    """Validate preview/create/update payloads for event series generation."""

    taxonomy_term_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    campaign = serializers.PrimaryKeyRelatedField(
        queryset=EventSeries._meta.get_field("campaign").remote_field.model.objects.all(),
        required=False,
    )
    event_type = serializers.PrimaryKeyRelatedField(
        queryset=EventType.objects.all(),
        required=False,
    )
    name = serializers.CharField(required=False, allow_blank=True, allow_null=False)
    default_context = serializers.PrimaryKeyRelatedField(
        queryset=GeoContext.objects.all(),
        required=False,
        allow_null=True,
    )
    series_mode = serializers.ChoiceField(
        choices=EventSeries.SeriesMode.choices,
        required=False,
    )
    recurrence_type = serializers.ChoiceField(
        choices=EventSeries.RecurrenceType.choices,
        required=False,
        allow_blank=True,
    )
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False, allow_null=True)
    occurrence_count = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    interval = serializers.IntegerField(required=False, min_value=1)
    start_time = serializers.TimeField(required=False)
    end_time = serializers.TimeField(required=False)
    timezone = serializers.CharField(required=False, allow_blank=False)
    monthly_rule_type = serializers.ChoiceField(
        choices=EventSeries.MonthlyRuleType.choices,
        required=False,
        allow_blank=True,
    )
    day_of_month = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=31)
    week_of_month = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=5)
    weekday_of_month = serializers.CharField(required=False, allow_blank=True)
    by_weekday = serializers.ListField(
        child=serializers.ChoiceField(choices=sorted(VALID_WEEKDAYS)),
        required=False,
        allow_empty=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    explicit_dates = serializers.ListField(
        child=serializers.DateField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    title = serializers.CharField(required=False, max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    location_mode = serializers.ChoiceField(
        choices=Event.LocationMode.choices,
        required=False,
    )
    location = serializers.JSONField(required=False, allow_null=True)
    online_url = serializers.URLField(required=False, allow_blank=True)
    online_platform = serializers.CharField(required=False, allow_blank=True)
    access_notes = serializers.CharField(required=False, allow_blank=True)
    provider_name = serializers.CharField(required=False, allow_blank=True)
    provider_url = serializers.URLField(required=False, allow_blank=True)
    provider_contact = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Event.Status.choices, required=False)
    visibility = serializers.ChoiceField(choices=Event.Visibility.choices, required=False)
    context = serializers.PrimaryKeyRelatedField(
        queryset=GeoContext.objects.all(),
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        taxonomy_term_ids = attrs.pop("taxonomy_term_ids", serializers.empty)
        if taxonomy_term_ids is not serializers.empty:
            attrs["_taxonomy_terms"] = self._resolve_taxonomy_terms(taxonomy_term_ids)

        series_attrs = self._merged_series_attrs(attrs)
        explicit_dates = self._resolved_explicit_dates(attrs)
        series_candidate = EventSeries(
            **series_attrs,
            created_by=self.context["request"].user,
        )
        series_candidate.clean()

        if self.instance and (
            "campaign" in series_attrs
            and series_attrs["campaign"] != self.instance.campaign
            and self.instance.events.exists()
        ):
            raise serializers.ValidationError(
                {"campaign": "Series campaign cannot be changed after occurrences exist."}
            )

        if self.instance and (
            "event_type" in series_attrs
            and series_attrs["event_type"] != self.instance.event_type
            and self.instance.events.exists()
        ):
            raise serializers.ValidationError(
                {"event_type": "Series event type cannot be changed after occurrences exist."}
            )

        try:
            occurrences = build_occurrence_specs(
                series_candidate,
                explicit_dates=explicit_dates,
            )
        except ZoneInfoNotFoundError as exc:
            raise serializers.ValidationError(
                {"timezone": f"Unknown timezone: {exc}"}
            ) from exc
        if not occurrences:
            raise serializers.ValidationError(
                {"explicit_dates": "This series definition produces no occurrences."}
            )

        event_template = self._merged_event_template(attrs)
        exemplar_event = self._template_event_instance(
            series=series_candidate,
            occurrence=occurrences[0],
            event_template=event_template,
        )
        exemplar_event.clean()

        attrs["_series_attrs"] = series_attrs
        attrs["_explicit_dates"] = explicit_dates
        attrs["_event_template"] = event_template
        attrs["_occurrences"] = occurrences
        attrs["_template_event"] = self._base_template_event()
        return attrs

    def create(self, validated_data):
        taxonomy_terms = validated_data.pop("_taxonomy_terms", [])
        series_attrs = validated_data.pop("_series_attrs")
        explicit_dates = validated_data.pop("_explicit_dates")
        event_template = validated_data.pop("_event_template")
        occurrences = validated_data.pop("_occurrences")

        with transaction.atomic():
            series = EventSeries.objects.create(
                **series_attrs,
                created_by=self.context["request"].user,
            )
            self._persist_explicit_dates(series, explicit_dates)
            created_events = create_occurrence_events(
                series=series,
                occurrences=occurrences,
                event_template=event_template,
                organizer=self.context["request"].user,
                taxonomy_terms=taxonomy_terms,
            )

        series._response_occurrences = serialize_occurrence_events(created_events)
        return series

    def update(self, instance, validated_data):
        taxonomy_terms = validated_data.pop("_taxonomy_terms", KEEP_EXISTING_TERMS)
        series_attrs = validated_data.pop("_series_attrs")
        explicit_dates = validated_data.pop("_explicit_dates")
        event_template = validated_data.pop("_event_template")
        occurrences = validated_data.pop("_occurrences")
        template_event = self._base_template_event()

        for field, value in series_attrs.items():
            setattr(instance, field, value)

        with transaction.atomic():
            instance.save()
            self._persist_explicit_dates(instance, explicit_dates)
            sync_result = sync_occurrence_events(
                series=instance,
                occurrences=occurrences,
                event_template=event_template,
                template_event=template_event,
                organizer=self.context["request"].user,
                taxonomy_terms=taxonomy_terms,
            )

        instance._response_occurrences = serialize_occurrence_events(sync_result.events)
        instance._sync_result = sync_result
        return instance

    def _merged_series_attrs(self, attrs):
        source = {}
        if self.instance:
            for field in EVENT_SERIES_FIELDS:
                source[field] = getattr(self.instance, field)

        for field in EVENT_SERIES_FIELDS:
            if field in attrs:
                source[field] = attrs[field]

        required_fields = {
            "campaign",
            "event_type",
            "start_date",
            "start_time",
            "end_time",
            "timezone",
            "series_mode",
        }
        missing = [
            field
            for field in required_fields
            if source.get(field) in (None, "")
        ]
        if missing:
            raise serializers.ValidationError(
                {field: "This field is required." for field in missing}
            )
        return source

    def _resolved_explicit_dates(self, attrs):
        explicit_dates = attrs.get("explicit_dates", serializers.empty)
        series_mode = attrs.get(
            "series_mode",
            self.instance.series_mode if self.instance else None,
        )
        if series_mode == EventSeries.SeriesMode.MANUAL_BATCH:
            if explicit_dates is serializers.empty:
                if self.instance:
                    explicit_dates = list(
                        self.instance.dates.order_by("display_order", "occurrence_date").values_list(
                            "occurrence_date",
                            flat=True,
                        )
                    )
                else:
                    explicit_dates = []
            if not explicit_dates:
                raise serializers.ValidationError(
                    {"explicit_dates": "Manual batch series require at least one explicit date."}
                )
            duplicates = [
                str(item)
                for item, count in Counter(explicit_dates).items()
                if count > 1
            ]
            if duplicates:
                raise serializers.ValidationError(
                    {"explicit_dates": f"Duplicate explicit dates are not allowed: {', '.join(duplicates)}"}
                )
            return explicit_dates

        if explicit_dates is not serializers.empty:
            raise serializers.ValidationError(
                {"explicit_dates": "Explicit dates are only valid for manual batch series."}
            )

        return []

    def _merged_event_template(self, attrs):
        source = {}
        base_event = self._base_template_event()
        if base_event is not None:
            for field in EVENT_TEMPLATE_FIELDS:
                source[field] = getattr(base_event, field)

        for field in EVENT_TEMPLATE_FIELDS:
            if field in attrs:
                source[field] = attrs[field]

        required_fields = {"title", "location_mode"}
        missing = [
            field
            for field in required_fields
            if source.get(field) in (None, "")
        ]
        if missing:
            raise serializers.ValidationError(
                {field: "This field is required." for field in missing}
            )
        return source

    def _template_event_instance(self, *, series, occurrence, event_template):
        return Event(
            campaign=series.campaign,
            event_type=series.event_type,
            organizer=self.context["request"].user,
            series=series if self.instance else None,
            occurrence_index=occurrence.occurrence_index,
            original_start_datetime=occurrence.original_start_datetime,
            start_datetime=occurrence.start_datetime,
            end_datetime=occurrence.end_datetime,
            **event_template,
        )

    def _base_template_event(self):
        if self.instance is None:
            return None
        base_event = (
            self.instance.events.filter(is_exception=False)
            .order_by("occurrence_index", "start_datetime")
            .prefetch_related("event_terms__term")
            .first()
        )
        if base_event is not None:
            return base_event
        return (
            self.instance.events.order_by("occurrence_index", "start_datetime")
            .prefetch_related("event_terms__term")
            .first()
        )

    def _persist_explicit_dates(self, series, explicit_dates):
        if series.series_mode != EventSeries.SeriesMode.MANUAL_BATCH:
            if series.pk:
                series.dates.all().delete()
            return

        series.dates.all().delete()
        EventSeriesDate.objects.bulk_create(
            [
                EventSeriesDate(
                    series=series,
                    occurrence_date=occurrence_date,
                    display_order=index,
                )
                for index, occurrence_date in enumerate(explicit_dates, start=1)
            ]
        )


# =============================================================================
# Spatial Filter Serializers
# =============================================================================


class BBoxSerializer(serializers.Serializer):
    """Validates shared event filters plus bbox query parameter."""

    campaign_id = serializers.UUIDField(required=False)
    dimension_id = serializers.UUIDField(required=False)
    term_id = serializers.UUIDField(required=False)
    include_past = serializers.BooleanField(default=False)
    start_after = serializers.DateTimeField(required=False)
    start_before = serializers.DateTimeField(required=False)
    status = serializers.ChoiceField(
        choices=Event.Status.choices,
        default=Event.Status.PUBLISHED,
    )
    visibility = serializers.ChoiceField(
        choices=Event.Visibility.choices,
        required=False,
    )
    bbox = serializers.CharField(required=False, allow_blank=True)

    def validate_bbox(self, value):
        """Parse bbox string into Polygon geometry."""
        if not value:
            return None

        try:
            parts = [float(x) for x in value.split(",")]
            if len(parts) != 4:
                raise ValueError("Must have 4 values")

            min_lon, min_lat, max_lon, max_lat = parts

            # Validate ranges
            if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
                raise ValueError("Longitude must be between -180 and 180")
            if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
                raise ValueError("Latitude must be between -90 and 90")
            if min_lon >= max_lon or min_lat >= max_lat:
                raise ValueError("Min must be less than max")

            # Create polygon from bbox
            return Polygon.from_bbox((min_lon, min_lat, max_lon, max_lat))

        except (ValueError, TypeError) as e:
            raise serializers.ValidationError(
                f"Invalid bbox format. Expected: min_lon,min_lat,max_lon,max_lat. Error: {e}"
            )


class GeometryFilterSerializer(serializers.Serializer):
    """
    Validates geometry filter for POST /events/within/ endpoint.
    Accepts GeoJSON geometry.
    """

    geometry = serializers.JSONField(required=True)
    campaign_id = serializers.UUIDField(required=False)
    dimension_id = serializers.UUIDField(required=False)
    term_id = serializers.UUIDField(required=False)
    include_past = serializers.BooleanField(default=False)
    start_after = serializers.DateTimeField(required=False)
    start_before = serializers.DateTimeField(required=False)
    status = serializers.ChoiceField(
        choices=Event.Status.choices,
        default=Event.Status.PUBLISHED,
    )
    visibility = serializers.ChoiceField(
        choices=Event.Visibility.choices,
        required=False,
    )

    def validate_geometry(self, value):
        """Parse GeoJSON into GEOS geometry."""
        try:
            import json

            geojson_str = json.dumps(value)
            geom = GEOSGeometry(geojson_str)

            # Only allow Polygon or MultiPolygon
            if geom.geom_type not in ("Polygon", "MultiPolygon"):
                raise serializers.ValidationError(
                    f"Geometry must be Polygon or MultiPolygon, got {geom.geom_type}"
                )

            # Ensure SRID is set
            if geom.srid is None:
                geom.srid = 4326

            return geom

        except Exception as e:
            raise serializers.ValidationError(f"Invalid GeoJSON geometry: {e}")
