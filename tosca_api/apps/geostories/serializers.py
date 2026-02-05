from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.layerrefs.models import LayerRef

from .models import GeoStory, GeoStoryLayer


# =============================================================================
# Nested Serializers (for Detail view)
# =============================================================================


class GeoContextSerializer(serializers.ModelSerializer):
    """Serializer for GeoContext - exposes content for reading."""

    class Meta:
        model = GeoContext
        fields = ["id", "content", "content_type"]
        read_only_fields = ["id", "content", "content_type"]


class LayerRefSerializer(serializers.ModelSerializer):
    """Serializer for LayerRef - exposes layer name."""

    class Meta:
        model = LayerRef
        fields = ["id", "layer_name"]
        read_only_fields = ["id", "layer_name"]


class GeoStoryLayerSerializer(serializers.ModelSerializer):
    """
    Serializer for GeoStoryLayer through model.
    Includes layer details and display order.
    """

    id = serializers.UUIDField(source="layer.id", read_only=True)
    layer_name = serializers.CharField(source="layer.layer_name", read_only=True)

    class Meta:
        model = GeoStoryLayer
        fields = ["id", "layer_name", "display_order"]
        read_only_fields = ["id", "layer_name", "display_order"]


class FeatureLinkSerializer(serializers.ModelSerializer):
    """
    Serializer for outgoing FeatureLinks.
    Shows target info for navigation.
    """

    target_type = serializers.SerializerMethodField()

    class Meta:
        model = FeatureLink
        fields = ["id", "target_content_type", "target_object_id", "target_type", "link_type"]
        read_only_fields = ["id", "target_content_type", "target_object_id", "target_type", "link_type"]

    def get_target_type(self, obj) -> str:
        """Return human-readable target type (e.g. 'geostory')."""
        return obj.target_content_type.model


# =============================================================================
# GeoStory Serializers
# =============================================================================


class GeoStoryListSerializer(serializers.ModelSerializer):
    """
    Slim serializer for GeoStory list view.
    Optimized for fast loading of story cards.
    """

    class Meta:
        model = GeoStory
        fields = [
            "id",
            "title",
            "summary",
            "campaign",
            "created_at",
        ]
        read_only_fields = fields


class GeoStoryDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for GeoStory detail view.
    Includes nested context, layers, and feature links.
    """

    context = GeoContextSerializer(read_only=True)
    layers = serializers.SerializerMethodField()
    feature_links = serializers.SerializerMethodField()

    class Meta:
        model = GeoStory
        fields = [
            "id",
            "title",
            "summary",
            "status",
            "campaign",
            "context",
            "layers",
            "feature_links",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_layers(self, obj) -> list:
        """
        Return layers ordered by display_order.
        Uses the through model to get ordering.
        """
        through_qs = GeoStoryLayer.objects.filter(geostory=obj).select_related("layer")
        return GeoStoryLayerSerializer(through_qs, many=True).data

    def get_feature_links(self, obj) -> list:
        """
        Return outgoing feature links (where this story is the source).
        """
        geostory_ct = ContentType.objects.get_for_model(GeoStory)
        links = FeatureLink.objects.filter(
            source_content_type=geostory_ct,
            source_object_id=obj.id,
        ).select_related("target_content_type")
        return FeatureLinkSerializer(links, many=True).data


class GeoStorySerializer(serializers.ModelSerializer):
    """
    Serializer for GeoStory model.
    Used for create/update operations (Admin/Editor use).
    """

    class Meta:
        model = GeoStory
        fields = [
            "id",
            "title",
            "summary",
            "status",
            "campaign",
            "author",
            "context",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "author", "context", "created_at", "updated_at"]
