from collections import Counter

from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.db import transaction
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from tosca_api.apps.geocontext.models import GeoContext

from .models import Event, EventLayer, EventTerm, TaxonomyDimension, TaxonomyTerm


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


class EventWriteSerializer(serializers.ModelSerializer):
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

        instance = self.instance or Event()
        for attr, value in attrs.items():
            if attr.startswith("_"):
                continue
            setattr(instance, attr, value)

        # Event.clean() enforces start_datetime <= end_datetime
        instance.clean()
        return attrs

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

    def create(self, validated_data):
        taxonomy_terms = validated_data.pop("_taxonomy_terms", serializers.empty)
        event = super().create(validated_data)
        if taxonomy_terms is not serializers.empty:
            self._replace_event_terms(event, taxonomy_terms)
        return event

    def update(self, instance, validated_data):
        taxonomy_terms = validated_data.pop("_taxonomy_terms", serializers.empty)
        event = super().update(instance, validated_data)
        if taxonomy_terms is not serializers.empty:
            self._replace_event_terms(event, taxonomy_terms)
        return event


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
