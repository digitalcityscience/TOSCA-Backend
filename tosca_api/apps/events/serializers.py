from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.utils import timezone
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from tosca_api.apps.geocontext.models import GeoContext

from .models import CalendarEvent, EventLayer


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
# CalendarEvent Serializers
# =============================================================================


class CalendarEventListSerializer(serializers.ModelSerializer):
    """
    Slim serializer for calendar view (list).
    Used when no spatial filtering is applied.
    """

    class Meta:
        model = CalendarEvent
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "start_datetime",
            "end_datetime",
            "status",
            "visibility",
            "created_at",
        ]
        read_only_fields = fields


class CalendarEventDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for event detail view.
    Includes nested context and layers.
    """

    context = EventGeoContextSerializer(read_only=True)
    layers = serializers.SerializerMethodField()

    class Meta:
        model = CalendarEvent
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "start_datetime",
            "end_datetime",
            "location",
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


class CalendarEventGeoSerializer(GeoFeatureModelSerializer):
    """
    GeoJSON serializer for map view.
    Returns events as GeoJSON FeatureCollection.
    """

    class Meta:
        model = CalendarEvent
        geo_field = "location"
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "start_datetime",
            "end_datetime",
            "status",
            "visibility",
        ]
        read_only_fields = fields


class CalendarEventCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating events."""

    class Meta:
        model = CalendarEvent
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "start_datetime",
            "end_datetime",
            "location",
            "status",
            "visibility",
            "organizer",
            "context",
        ]
        read_only_fields = ["id", "organizer"]


# =============================================================================
# Spatial Filter Serializers
# =============================================================================


class BBoxSerializer(serializers.Serializer):
    """Validates and parses bbox query parameter."""

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
    include_past = serializers.BooleanField(default=False)
    start_after = serializers.DateTimeField(required=False)
    start_before = serializers.DateTimeField(required=False)
    status = serializers.ChoiceField(
        choices=CalendarEvent.Status.choices,
        default=CalendarEvent.Status.PUBLISHED,
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
