from rest_framework import serializers
from rest_framework_gis.fields import GeometryField
from tosca_api.apps.geocontext.models import GeoContext

from .models import FeedbackLayer, FeedbackSubmission, GeoFeedback


class FeedbackGeoContextSerializer(serializers.ModelSerializer):
    """Nested serializer for feedback's GeoContext."""

    class Meta:
        model = GeoContext
        fields = ["id", "content", "content_type"]
        read_only_fields = fields


class FeedbackLayerSerializer(serializers.ModelSerializer):
    """Serializer for FeedbackLayer through model."""

    id = serializers.UUIDField(source="layer.id", read_only=True)
    layer_name = serializers.CharField(source="layer.layer_name", read_only=True)

    class Meta:
        model = FeedbackLayer
        fields = ["id", "layer_name", "display_order"]
        read_only_fields = fields


class GeoFeedbackListSerializer(serializers.ModelSerializer):
    """Slim serializer for listing feedback campaigns."""

    class Meta:
        model = GeoFeedback
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "status",
            "visibility",
            "rating_enabled",
            "form_enabled",
            "allow_drawings",
            "created_at",
        ]


class GeoFeedbackDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for reading feedback details.
    Includes form references and layers.
    """

    context = FeedbackGeoContextSerializer(read_only=True)
    layers = serializers.SerializerMethodField()
    custom_form_slug = serializers.CharField(
        source="custom_form.slug", read_only=True, allow_null=True
    )

    class Meta:
        model = GeoFeedback
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "context",
            "custom_form",
            "custom_form_slug",
            "rating_enabled",
            "form_enabled",
            "allow_drawings",
            "status",
            "visibility",
            "created_by",
            "layers",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_layers(self, obj) -> list:
        """Return layers ordered by display_order."""
        through_qs = FeedbackLayer.objects.filter(feedback=obj).select_related("layer")
        return FeedbackLayerSerializer(through_qs, many=True).data


class GeoFeedbackCreateUpdateSerializer(serializers.ModelSerializer):
    """Write serializer for creating or updating GeoFeedback."""

    class Meta:
        model = GeoFeedback
        fields = [
            "id",
            "title",
            "description",
            "campaign",
            "context",
            "custom_form",
            "rating_enabled",
            "form_enabled",
            "allow_drawings",
            "status",
            "visibility",
        ]
        read_only_fields = ["id", "created_by"]

    def validate(self, attrs):
        # We manually trigger model clean inside ModelViewSet perform_create
        # but DRF validate() can also do some basic validation here if we want
        return super().validate(attrs)



class FeedbackSubmissionSerializer(serializers.ModelSerializer):
    """
    Serializer for taking citizen submissions. 
    It supports creating geometry via GeoJSON.
    """

    form_data = serializers.JSONField(required=False, allow_null=True)
    geometry = GeometryField(required=False, allow_null=True)

    class Meta:
        model = FeedbackSubmission
        fields = [
            "id",
            "feedback",
            "submitted_by",
            "rating",
            "form_data",
            "geometry",
            "is_anonymized",
            "created_at",
        ]
        read_only_fields = ["id", "feedback", "submitted_by", "created_at"]

    def validate(self, attrs):
        # Validation dependent on feedback is done in clean() or in the view.
        # But we can also check it here if we inject self.context['feedback'] 
        return super().validate(attrs)
