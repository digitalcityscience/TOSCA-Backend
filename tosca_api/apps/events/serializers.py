import json
from collections import Counter
from zoneinfo import ZoneInfoNotFoundError

from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geodata_providers.api.serializers import (
    LayerSummarySerializer,
    LayerUUIDListField,
)

from .models import (
    Event,
    EventLayer,
    EventSeries,
    EventTerm,
    EventType,
    LANGUAGE_CHOICES,
    PublicHealthEventProfile,
    TaxonomyDimension,
    TaxonomyTerm,
    VALID_WEEKDAYS,
)
from .services import (
    EVENT_TEMPLATE_FIELDS,
    KEEP_EXISTING_TERMS,
    build_occurrence_specs,
    get_event_taxonomy_assignments,
    get_base_template_event,
    orchestrate_series_create,
    orchestrate_series_update,
    persist_explicit_dates,
    resolve_series_navigation,
    resolve_taxonomy_assignments,
    serialize_occurrence_events,
    serialize_taxonomy_assignments,
    validate_publish_requirements,
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

EXCEPTION_TRIGGER_FIELDS = {
    "title",
    "summary",
    "start_datetime",
    "end_datetime",
    "location_mode",
    "location",
    "venue_address",
    "district",
    "online_url",
    "online_platform",
    "access_notes",
    "provider_name",
    "provider_address",
    "provider_phone",
    "provider_email",
    "provider_social",
    "provider_url",
    "language",
    "language_note",
    "lead_name",
    "external_url",
    "status",
    "visibility",
}


# =============================================================================
# Nested Serializers
# =============================================================================


class EventGeoContextSerializer(serializers.ModelSerializer):
    """Nested serializer for event's GeoContext."""

    class Meta:
        model = GeoContext
        fields = ["id", "title", "content"]
        read_only_fields = fields


PROFILE_KEY_PUBLIC_HEALTH = "public_health"


class PublicHealthProfileSerializer(serializers.ModelSerializer):
    """Typed profile payload for events with event_type.profile_key='public_health'."""

    class Meta:
        model = PublicHealthEventProfile
        fields = [
            "target_age_note",
            "registration",
            "short_notice_possible",
            "cost_amount_eur",
            "reduced_amount_eur",
            "subsidy_program",
            "transit_note",
            "insurance_eligible",
            "referral_required",
        ]

    def validate(self, attrs):
        cost = attrs.get("cost_amount_eur")
        reduced = attrs.get("reduced_amount_eur")
        if cost is not None and cost < 0:
            raise serializers.ValidationError(
                {"cost_amount_eur": "cost_amount_eur must be non-negative."}
            )
        if reduced is not None and reduced < 0:
            raise serializers.ValidationError(
                {"reduced_amount_eur": "reduced_amount_eur must be non-negative."}
            )
        if cost is not None and reduced is not None and reduced > cost:
            raise serializers.ValidationError(
                {"reduced_amount_eur": "reduced_amount_eur cannot exceed cost_amount_eur."}
            )
        return attrs


def _profile_key_for_event_type(event_type) -> str:
    if event_type is None:
        return ""
    return event_type.profile_key or ""


class EventFeatureLinkSerializer(serializers.ModelSerializer):
    """Outgoing FeatureLink with target type hydrated for navigation."""

    target_type = serializers.SerializerMethodField()

    class Meta:
        model = FeatureLink
        fields = ["id", "target_content_type", "target_object_id", "target_type", "link_type"]
        read_only_fields = fields

    def get_target_type(self, obj) -> str:
        return obj.target_content_type.model


class EventLayerSerializer(serializers.ModelSerializer):
    """
    Serializer for EventLayer through model.

    Embeds the canonical Layer summary plus per-event display_order.
    """

    layer = LayerSummarySerializer(read_only=True)

    class Meta:
        model = EventLayer
        fields = ["layer", "display_order"]
        read_only_fields = fields


class EventTypeRegistrySerializer(serializers.ModelSerializer):
    """Public event-type registry payload for frontend filter/bootstrap state."""

    profile_key = serializers.SerializerMethodField()

    class Meta:
        model = EventType
        fields = ["id", "code", "label", "profile_mode", "profile_key"]
        read_only_fields = fields

    def get_profile_key(self, obj) -> str:
        return obj.profile_key or ""


class EventTaxonomyTermRegistrySerializer(serializers.ModelSerializer):
    """Public taxonomy term payload for frontend filters."""

    parent_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = TaxonomyTerm
        fields = ["id", "code", "label", "parent_id", "is_active"]
        read_only_fields = fields


class EventTaxonomyDimensionRegistrySerializer(serializers.ModelSerializer):
    """Public taxonomy dimension payload with active terms."""

    terms = EventTaxonomyTermRegistrySerializer(many=True, read_only=True)

    class Meta:
        model = TaxonomyDimension
        fields = ["id", "code", "label", "selection_mode", "terms"]
        read_only_fields = fields


def _compact_taxonomy_assignments(obj) -> list[dict]:
    """Return list/map taxonomy chips without UUID/edit-only metadata."""
    taxonomy_terms = [
        event_term.term
        for event_term in obj.event_terms.all()
        if event_term.term is not None
    ]
    assignments = serialize_taxonomy_assignments(taxonomy_terms)
    return [
        {
            "dimension_code": assignment["dimension_code"],
            "dimension_label": assignment["dimension_label"],
            "terms": [
                {
                    "code": term["code"],
                    "label": term["label"],
                }
                for term in assignment["terms"]
            ],
        }
        for assignment in assignments
    ]


# =============================================================================
# Event Serializers
# =============================================================================


class EventListSerializer(serializers.ModelSerializer):
    """
    Slim serializer for calendar view (list).
    Used when no spatial filtering is applied.
    """

    series_id = serializers.UUIDField(source="series.id", read_only=True, allow_null=True)
    series_name = serializers.CharField(source="series.name", read_only=True, default="")
    total_occurrences = serializers.SerializerMethodField()
    profile_key = serializers.SerializerMethodField()
    taxonomy_assignments = serializers.SerializerMethodField()
    effective_visibility = serializers.CharField(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "summary",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "status",
            "visibility",
            "effective_visibility",
            "series_id",
            "series_name",
            "occurrence_index",
            "total_occurrences",
            "is_exception",
            "profile_key",
            "taxonomy_assignments",
            "created_at",
        ]
        read_only_fields = fields

    def get_profile_key(self, obj) -> str:
        return _profile_key_for_event_type(obj.event_type)

    def get_taxonomy_assignments(self, obj) -> list[dict]:
        return _compact_taxonomy_assignments(obj)

    def get_total_occurrences(self, obj) -> int | None:
        if obj.series_id is None:
            return None
        annotated = getattr(obj, "series_total_occurrences", None)
        if annotated is not None:
            return annotated
        return obj.series.events.count() if obj.series_id else None


class EventDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for event detail view.
    Includes nested context and layers.
    """

    context = serializers.SerializerMethodField()
    layers = serializers.SerializerMethodField()
    taxonomy_assignments = serializers.SerializerMethodField()
    series = serializers.SerializerMethodField()
    feature_links = serializers.SerializerMethodField()
    profile_key = serializers.SerializerMethodField()
    profile = serializers.SerializerMethodField()
    effective_visibility = serializers.CharField(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "summary",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "location",
            "venue_address",
            "district",
            "online_url",
            "online_platform",
            "access_notes",
            "provider_name",
            "provider_address",
            "provider_phone",
            "provider_email",
            "provider_social",
            "provider_url",
            "language",
            "language_note",
            "lead_name",
            "external_url",
            "series",
            "status",
            "visibility",
            "effective_visibility",
            "organizer",
            "context",
            "profile_key",
            "profile",
            "taxonomy_assignments",
            "layers",
            "feature_links",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_profile_key(self, obj) -> str:
        return _profile_key_for_event_type(obj.event_type)

    def get_profile(self, obj):
        profile_key = _profile_key_for_event_type(obj.event_type)
        if profile_key == PROFILE_KEY_PUBLIC_HEALTH:
            try:
                profile = obj.public_health_profile
            except PublicHealthEventProfile.DoesNotExist:
                return None
            return PublicHealthProfileSerializer(profile).data
        return None

    def get_series(self, obj):
        return resolve_series_navigation(obj)

    def get_feature_links(self, obj) -> list:
        event_ct = ContentType.objects.get_for_model(Event)
        links = FeatureLink.objects.filter(
            source_content_type=event_ct,
            source_object_id=obj.id,
        ).select_related("target_content_type")
        return EventFeatureLinkSerializer(links, many=True).data

    def get_layers(self, obj) -> list:
        """Return layers ordered by display_order with full Layer summary."""
        through_qs = EventLayer.objects.filter(event=obj).select_related(
            "layer__workspace"
        )
        return EventLayerSerializer(through_qs, many=True).data

    def get_context(self, obj):
        """Return the resolved event context."""
        context = obj.effective_context
        if context is None:
            return None
        return EventGeoContextSerializer(context).data

    def get_taxonomy_assignments(self, obj):
        assignments = get_event_taxonomy_assignments(obj)
        return TaxonomyAssignmentReadSerializer(assignments, many=True).data


class EventGeoSerializer(GeoFeatureModelSerializer):
    """
    GeoJSON serializer for map view.
    Returns events as GeoJSON FeatureCollection.
    """

    series_id = serializers.UUIDField(source="series.id", read_only=True, allow_null=True)
    series_name = serializers.CharField(source="series.name", read_only=True, default="")
    total_occurrences = serializers.SerializerMethodField()
    profile_key = serializers.SerializerMethodField()
    taxonomy_assignments = serializers.SerializerMethodField()
    effective_visibility = serializers.CharField(read_only=True)

    class Meta:
        model = Event
        geo_field = "location"
        fields = [
            "id",
            "title",
            "summary",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "status",
            "visibility",
            "effective_visibility",
            "series_id",
            "series_name",
            "occurrence_index",
            "total_occurrences",
            "is_exception",
            "profile_key",
            "taxonomy_assignments",
        ]
        read_only_fields = fields

    def get_profile_key(self, obj) -> str:
        return _profile_key_for_event_type(obj.event_type)

    def get_taxonomy_assignments(self, obj) -> list[dict]:
        return _compact_taxonomy_assignments(obj)

    def get_total_occurrences(self, obj) -> int | None:
        if obj.series_id is None:
            return None
        annotated = getattr(obj, "series_total_occurrences", None)
        if annotated is not None:
            return annotated
        return obj.series.events.count() if obj.series_id else None


class EventMapOnlineSerializer(serializers.ModelSerializer):
    """Serializer for online events returned in the dedicated map endpoint."""

    series_id = serializers.UUIDField(source="series.id", read_only=True, allow_null=True)
    series_name = serializers.CharField(source="series.name", read_only=True, default="")
    total_occurrences = serializers.SerializerMethodField()
    profile_key = serializers.SerializerMethodField()
    taxonomy_assignments = serializers.SerializerMethodField()
    effective_visibility = serializers.CharField(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "summary",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "online_url",
            "online_platform",
            "status",
            "visibility",
            "effective_visibility",
            "series_id",
            "series_name",
            "occurrence_index",
            "total_occurrences",
            "is_exception",
            "profile_key",
            "taxonomy_assignments",
        ]
        read_only_fields = fields

    def get_profile_key(self, obj) -> str:
        return _profile_key_for_event_type(obj.event_type)

    def get_taxonomy_assignments(self, obj) -> list[dict]:
        return _compact_taxonomy_assignments(obj)

    def get_total_occurrences(self, obj) -> int | None:
        if obj.series_id is None:
            return None
        annotated = getattr(obj, "series_total_occurrences", None)
        if annotated is not None:
            return annotated
        return obj.series.events.count() if obj.series_id else None


class TaxonomyAssignmentSerializer(serializers.Serializer):
    """Single grouped taxonomy assignment entry."""

    dimension_id = serializers.UUIDField()
    term_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )


class TaxonomyAssignmentReadTermSerializer(serializers.Serializer):
    """Hydrated taxonomy term payload for read responses."""

    id = serializers.UUIDField()
    code = serializers.CharField()
    label = serializers.CharField()
    parent_id = serializers.UUIDField(allow_null=True)
    is_active = serializers.BooleanField()


class TaxonomyAssignmentReadSerializer(serializers.Serializer):
    """Grouped taxonomy assignment payload for read responses."""

    dimension_id = serializers.UUIDField()
    dimension_code = serializers.CharField()
    dimension_label = serializers.CharField()
    selection_mode = serializers.CharField()
    profile_key = serializers.CharField(allow_blank=True)
    term_ids = serializers.ListField(child=serializers.UUIDField())
    terms = TaxonomyAssignmentReadTermSerializer(many=True)


class TaxonomyAssignmentResolutionMixin:
    """Shared taxonomy assignment validation and replacement helpers."""

    def _resolve_taxonomy_assignments(
        self,
        taxonomy_assignments,
        *,
        event_profile_key: str | None = None,
    ):
        try:
            return resolve_taxonomy_assignments(
                taxonomy_assignments,
                event_profile_key=event_profile_key,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc

    def _replace_event_terms(self, event, taxonomy_terms):
        with transaction.atomic():
            EventTerm.objects.filter(event=event).delete()
            for term in taxonomy_terms:
                EventTerm.objects.create(event=event, term=term)


class EventWriteSerializer(TaxonomyAssignmentResolutionMixin, serializers.ModelSerializer):
    """Serializer for creating/updating events."""

    taxonomy_assignments = TaxonomyAssignmentSerializer(
        many=True,
        required=False,
        write_only=True,
    )
    layers = LayerUUIDListField(required=False, write_only=True)
    profile = serializers.DictField(required=False, write_only=True)
    # Deprecated (epic-11 §3.2): visibility is no longer writable via the
    # API -- Campaign.visibility is the sole authority. Declared explicitly
    # (rather than left to the ModelSerializer default, which would make it
    # writable) and ignored on write; see effective_visibility for the
    # value clients should actually use.
    visibility = serializers.CharField(read_only=True)
    effective_visibility = serializers.CharField(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "summary",
            "campaign",
            "event_type",
            "start_datetime",
            "end_datetime",
            "location_mode",
            "location",
            "venue_address",
            "district",
            "online_url",
            "online_platform",
            "access_notes",
            "provider_name",
            "provider_address",
            "provider_phone",
            "provider_email",
            "provider_social",
            "provider_url",
            "language",
            "language_note",
            "lead_name",
            "external_url",
            "series",
            "occurrence_index",
            "is_exception",
            "original_start_datetime",
            "status",
            "visibility",
            "effective_visibility",
            "organizer",
            "context",
            "taxonomy_assignments",
            "layers",
            "profile",
        ]
        read_only_fields = ["id", "organizer"]

    def validate(self, attrs):
        """Invoke model clean() to ensure DB constraints surface as API 400s."""
        campaign = attrs.get("campaign")
        if campaign is None and self.instance is not None:
            campaign = self.instance.campaign
        request = self.context.get("request")
        if request is not None:
            from tosca_api.apps.organizations.permissions import (
                validate_campaign_organization,
            )

            if not validate_campaign_organization(request, campaign):
                raise serializers.ValidationError(
                    {"campaign": "Campaign does not belong to your organization."}
                )

        taxonomy_assignments = attrs.pop("taxonomy_assignments", serializers.empty)
        event_type = attrs.get("event_type")
        if event_type is None and self.instance is not None:
            event_type = self.instance.event_type
        event_profile_key = (event_type.profile_key or "") if event_type else ""

        if taxonomy_assignments is not serializers.empty:
            attrs["_taxonomy_terms"] = self._resolve_taxonomy_assignments(
                taxonomy_assignments,
                event_profile_key=event_profile_key,
            )

        raw_profile = attrs.pop("profile", serializers.empty)
        if raw_profile is not serializers.empty:
            if event_profile_key != PROFILE_KEY_PUBLIC_HEALTH:
                raise serializers.ValidationError(
                    {
                        "profile": (
                            "A `profile` payload is only accepted for events whose "
                            "event_type.profile_key='public_health'."
                        )
                    }
                )
            profile_serializer = PublicHealthProfileSerializer(data=raw_profile)
            if not profile_serializer.is_valid():
                raise serializers.ValidationError({"profile": profile_serializer.errors})
            attrs["_profile_data"] = dict(profile_serializer.validated_data)

        instance = Event()
        if self.instance is not None:
            for field in self.Meta.fields:
                # effective_visibility is a read-only derived property (no
                # setter) -- excluded alongside the other non-model fields
                # already skipped here.
                if field in {
                    "id",
                    "taxonomy_assignments",
                    "layers",
                    "profile",
                    "effective_visibility",
                }:
                    continue
                setattr(instance, field, getattr(self.instance, field))
            instance.pk = self.instance.pk

        for attr, value in attrs.items():
            if attr.startswith("_") or attr in {"layers", "profile"}:
                continue
            setattr(instance, attr, value)

        # Event.clean() enforces start_datetime <= end_datetime
        instance.clean()

        publish_errors = validate_publish_requirements(
            {
                "status": instance.status,
                "summary": instance.summary,
                "provider_phone": instance.provider_phone,
                "provider_email": instance.provider_email,
                "provider_social": instance.provider_social,
                "provider_url": instance.provider_url,
            }
        )
        if publish_errors:
            raise serializers.ValidationError(publish_errors)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        taxonomy_terms = validated_data.pop("_taxonomy_terms", serializers.empty)
        layers = validated_data.pop("layers", None)
        profile_data = validated_data.pop("_profile_data", None)
        event = super().create(validated_data)
        if taxonomy_terms is not serializers.empty:
            self._replace_event_terms(event, taxonomy_terms)
        if layers is not None:
            self._sync_layers(event, layers)
        if profile_data is not None:
            self._upsert_public_health_profile(event, profile_data)
        return event

    @transaction.atomic
    def update(self, instance, validated_data):
        taxonomy_terms = validated_data.pop("_taxonomy_terms", serializers.empty)
        layers = validated_data.pop("layers", None)
        profile_data = validated_data.pop("_profile_data", None)
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
        if layers is not None:
            self._sync_layers(event, layers)
        if profile_data is not None:
            self._upsert_public_health_profile(event, profile_data)
        return event

    @staticmethod
    def _upsert_public_health_profile(event: Event, profile_data: dict) -> None:
        PublicHealthEventProfile.objects.update_or_create(
            event=event,
            defaults=profile_data,
        )

    @staticmethod
    def _sync_layers(event: Event, layers: list) -> None:
        """Replace the event's EventLayer rows with the supplied list."""
        EventLayer.objects.filter(event=event).delete()
        for index, layer in enumerate(layers):
            EventLayer.objects.create(
                event=event, layer=layer, display_order=index
            )

    def _should_mark_exception(self, instance, validated_data, taxonomy_terms) -> bool:
        if not instance.series_id:
            return False

        for field in EXCEPTION_TRIGGER_FIELDS:
            if field in validated_data and validated_data[field] != getattr(instance, field):
                return True

        if "context" in validated_data:
            incoming_context = validated_data["context"]
            incoming_context_id = (
                incoming_context.id if incoming_context is not None else None
            )
            if incoming_context_id != instance.context_id:
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
    taxonomy_assignments = serializers.SerializerMethodField()

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
            "taxonomy_assignments",
            "occurrences",
        ]
        read_only_fields = fields

    def get_occurrences(self, obj):
        occurrences = getattr(obj, "_response_occurrences", [])
        return EventSeriesOccurrenceSerializer(occurrences, many=True).data

    def get_taxonomy_assignments(self, obj):
        base_event = get_base_template_event(obj)
        if base_event is None:
            return []
        assignments = get_event_taxonomy_assignments(base_event)
        return TaxonomyAssignmentReadSerializer(assignments, many=True).data


class EventSeriesWriteSerializer(TaxonomyAssignmentResolutionMixin, serializers.Serializer):
    """Validate preview/create/update payloads for event series generation."""

    taxonomy_assignments = TaxonomyAssignmentSerializer(
        many=True,
        required=False,
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
    summary = serializers.CharField(required=False, allow_blank=True, max_length=100)
    location_mode = serializers.ChoiceField(
        choices=Event.LocationMode.choices,
        required=False,
    )
    location = serializers.JSONField(required=False, allow_null=True)
    venue_address = serializers.CharField(required=False, allow_blank=True, max_length=512)
    district = serializers.CharField(required=False, allow_blank=True, max_length=120)
    online_url = serializers.URLField(required=False, allow_blank=True)
    online_platform = serializers.CharField(required=False, allow_blank=True)
    access_notes = serializers.CharField(required=False, allow_blank=True)
    provider_name = serializers.CharField(required=False, allow_blank=True)
    provider_address = serializers.CharField(required=False, allow_blank=True, max_length=512)
    provider_phone = serializers.CharField(required=False, allow_blank=True, max_length=50)
    provider_email = serializers.EmailField(required=False, allow_blank=True)
    provider_social = serializers.CharField(required=False, allow_blank=True, max_length=512)
    provider_url = serializers.URLField(required=False, allow_blank=True)
    language = serializers.ListField(
        child=serializers.ChoiceField(choices=[code for code, _ in LANGUAGE_CHOICES]),
        required=False,
        allow_empty=True,
    )
    language_note = serializers.CharField(required=False, allow_blank=True, max_length=255)
    lead_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    external_url = serializers.URLField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Event.Status.choices, required=False)
    visibility = serializers.ChoiceField(choices=Event.Visibility.choices, required=False)
    context = serializers.PrimaryKeyRelatedField(
        queryset=GeoContext.objects.all(),
        required=False,
        allow_null=True,
    )

    def validate_location(self, value):
        """Validate and normalize series template location input as a GeoJSON point."""
        if value is None:
            return None

        try:
            geometry = GEOSGeometry(json.dumps(value))
        except Exception as exc:
            raise serializers.ValidationError(
                f"Invalid GeoJSON point: {exc}"
            ) from exc

        if geometry.geom_type != "Point":
            raise serializers.ValidationError("Location must be a GeoJSON Point.")

        if geometry.srid is None:
            geometry.srid = 4326
        elif geometry.srid != 4326:
            geometry.transform(4326)

        return geometry

    def validate(self, attrs):
        taxonomy_assignments = attrs.pop("taxonomy_assignments", serializers.empty)
        if taxonomy_assignments is not serializers.empty:
            event_type = attrs.get("event_type") or (
                self.instance.event_type if self.instance else None
            )
            event_profile_key = (event_type.profile_key or "") if event_type else ""
            attrs["_taxonomy_terms"] = self._resolve_taxonomy_assignments(
                taxonomy_assignments,
                event_profile_key=event_profile_key,
            )

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
        validated_data.pop("_occurrences")
        validated_data.pop("_template_event", None)

        with transaction.atomic():
            series = EventSeries.objects.create(
                **series_attrs,
                created_by=self.context["request"].user,
            )
            persist_explicit_dates(series, explicit_dates)
            created_events = orchestrate_series_create(
                series=series,
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
        validated_data.pop("_occurrences")
        validated_data.pop("_template_event", None)

        for field, value in series_attrs.items():
            setattr(instance, field, value)

        with transaction.atomic():
            instance.save()
            persist_explicit_dates(instance, explicit_dates)
            sync_result = orchestrate_series_update(
                series=instance,
                event_template=event_template,
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
        return get_base_template_event(self.instance)

    def _persist_explicit_dates(self, series, explicit_dates):
        persist_explicit_dates(series, explicit_dates)


# =============================================================================
# Spatial Filter Serializers
# =============================================================================


class BBoxSerializer(serializers.Serializer):
    """Validates shared event filters plus bbox query parameter."""

    campaign_id = serializers.UUIDField(required=False)
    event_type_id = serializers.UUIDField(required=False)
    dimension_id = serializers.UUIDField(required=False)
    term_id = serializers.UUIDField(required=False)
    profile_key = serializers.CharField(required=False, allow_blank=True)
    dimension_code = serializers.CharField(required=False, allow_blank=False)
    term_code = serializers.CharField(required=False, allow_blank=False)
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
    event_type_id = serializers.UUIDField(required=False)
    dimension_id = serializers.UUIDField(required=False)
    term_id = serializers.UUIDField(required=False)
    profile_key = serializers.CharField(required=False, allow_blank=True)
    dimension_code = serializers.CharField(required=False, allow_blank=False)
    term_code = serializers.CharField(required=False, allow_blank=False)
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
