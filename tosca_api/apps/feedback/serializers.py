from django.db import transaction
from rest_framework import serializers
from rest_framework_gis.fields import GeometryField
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geodata_providers.api.serializers import (
    LayerSummarySerializer,
    LayerUUIDListField,
)

from .models import FeedbackLayer, FeedbackSubmission, GeoFeedback


class FeedbackGeoContextSerializer(serializers.ModelSerializer):
    """Nested serializer for feedback's GeoContext."""

    class Meta:
        model = GeoContext
        fields = ["id", "title", "content"]
        read_only_fields = fields


class FeedbackLayerSerializer(serializers.ModelSerializer):
    """
    Serializer for FeedbackLayer through model.

    Embeds the canonical Layer summary plus per-feedback display_order.
    """

    layer = LayerSummarySerializer(read_only=True)

    class Meta:
        model = FeedbackLayer
        fields = ["layer", "display_order"]
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
        read_only_fields = fields


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
        """Return layers ordered by display_order with full Layer summary."""
        through_qs = FeedbackLayer.objects.filter(feedback=obj).select_related(
            "layer__workspace"
        )
        return FeedbackLayerSerializer(through_qs, many=True).data


class GeoFeedbackWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating or updating GeoFeedback.

    Accepts an optional ``layers`` list of ``geodata_providers.Layer`` UUIDs.
    Order of UUIDs in the list becomes the per-feedback display_order.
    Layers must be public + published — see ``LayerUUIDListField``.
    """

    layers = LayerUUIDListField(required=False, write_only=True)

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
            "layers",
        ]
        read_only_fields = ["id", "created_by"]

    def validate(self, attrs):
        """Invoke model clean() for DB-level validation."""
        layer_attrs = {k: v for k, v in attrs.items() if k != "layers"}
        instance = GeoFeedback(**layer_attrs)
        if self.instance:
            for attr, value in layer_attrs.items():
                setattr(instance, attr, value)

        instance.clean()
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        layers = validated_data.pop("layers", None)
        feedback = super().create(validated_data)
        if layers is not None:
            self._sync_layers(feedback, layers)
        return feedback

    @transaction.atomic
    def update(self, instance, validated_data):
        layers = validated_data.pop("layers", None)
        feedback = super().update(instance, validated_data)
        if layers is not None:
            self._sync_layers(feedback, layers)
        return feedback

    @staticmethod
    def _sync_layers(feedback: GeoFeedback, layers: list) -> None:
        """Replace the feedback's FeedbackLayer rows with the supplied list."""
        FeedbackLayer.objects.filter(feedback=feedback).delete()
        for index, layer in enumerate(layers):
            FeedbackLayer.objects.create(
                feedback=feedback, layer=layer, display_order=index
            )



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
        """Invoke model clean() for submission-level validation."""
        instance = FeedbackSubmission(**attrs)
        # Inject the feedback instance from context (set by ViewSet)
        if 'feedback' in self.context:
            instance.feedback = self.context['feedback']
            
        instance.clean()
        return attrs
