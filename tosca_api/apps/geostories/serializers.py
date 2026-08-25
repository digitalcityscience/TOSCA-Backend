import copy

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from tosca_api.apps.core.image_policy import validate_hero_image
from tosca_api.apps.core.editorjs import render_content_media_urls
from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.geodata_providers.api.serializers import (
    LayerSummarySerializer,
)
from tosca_api.apps.geodata_providers.models import Layer, LayerStyleAssignment

from .models import GeoStory, GeoStoryLayer


def _absolute_hero_image_url(obj: GeoStory, request) -> str | None:
    """Resolve hero image storage value into an absolute URL when possible."""
    if not obj.hero_image:
        return None
    url = obj.hero_image.url
    if request is not None:
        return request.build_absolute_uri(url)
    return url


# =============================================================================
# Nested Serializers (for Detail view)
# =============================================================================


class GeoStoryLayerSerializer(serializers.ModelSerializer):
    """
    Serializer for GeoStoryLayer through model.

    Embeds the canonical Layer summary (id, name, workspace, geometry_type,
    srid, published_url, is_public, publishing_state) plus the per-story
    display_order.
    """

    layer = LayerSummarySerializer(read_only=True)
    style_assignment = serializers.SerializerMethodField()

    class Meta:
        model = GeoStoryLayer
        fields = ["layer", "style_assignment", "display_order"]
        read_only_fields = fields

    def get_style_assignment(self, obj) -> dict | None:
        assignment = obj.style_assignment
        if assignment is None:
            return None
        style = assignment.style
        return {
            "id": str(assignment.id),
            "style_id": str(style.id),
            "name": style.name,
            "qualified_name": style.qualified_name,
            "role": assignment.role,
            "format": style.format,
            "style_layer_ids": assignment.style_layer_ids,
        }


class GeoStoryLayerListField(serializers.Field):
    """Accept legacy layer UUIDs or layer/style selections for a story."""

    default_error_messages = {
        "not_list": "Expected a list of layer UUIDs or layer objects.",
        "invalid_item": (
            "Each layer must be a UUID or an object containing 'layer' and, "
            "optionally, 'style_assignment' and 'display_order'."
        ),
    }

    def to_internal_value(self, data):
        from tosca_api.apps.geodata_providers.validators import (
            validate_layer_is_public_and_published,
        )

        if not isinstance(data, list):
            self.fail("not_list")

        normalized = []
        seen_layers = set()
        for index, item in enumerate(data):
            if isinstance(item, dict):
                unexpected = set(item) - {
                    "layer",
                    "layer_id",
                    "style_assignment",
                    "style_assignment_id",
                    "display_order",
                }
                layer_value = item.get("layer", item.get("layer_id"))
                assignment_value = item.get("style_assignment", item.get("style_assignment_id"))
                display_order = item.get("display_order", index)
                if unexpected or layer_value is None:
                    self.fail("invalid_item")
            else:
                layer_value = item
                assignment_value = None
                display_order = index

            try:
                layer_id = serializers.UUIDField().run_validation(layer_value)
                display_order = serializers.IntegerField(min_value=0).run_validation(display_order)
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({index: exc.detail}) from exc

            try:
                layer = Layer.objects.get(pk=layer_id)
            except Layer.DoesNotExist as exc:
                raise serializers.ValidationError({index: f"Unknown layer id: {layer_id}"}) from exc
            try:
                validate_layer_is_public_and_published(layer)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    {index: exc.message_dict.get("layer", exc.messages)}
                ) from exc

            assignment = None
            if assignment_value not in (None, ""):
                try:
                    assignment_id = serializers.UUIDField().run_validation(assignment_value)
                    assignment = LayerStyleAssignment.objects.select_related("style").get(
                        pk=assignment_id
                    )
                except LayerStyleAssignment.DoesNotExist as exc:
                    raise serializers.ValidationError(
                        {index: f"Unknown style assignment id: {assignment_value}"}
                    ) from exc
                except serializers.ValidationError as exc:
                    raise serializers.ValidationError({index: exc.detail}) from exc
                if assignment.layer_id != layer.id:
                    raise serializers.ValidationError(
                        {index: "Style assignment must belong to the selected layer."}
                    )

            if layer.id in seen_layers:
                raise serializers.ValidationError({index: "A layer can only be added once."})
            seen_layers.add(layer.id)
            normalized.append(
                {
                    "layer": layer,
                    "style_assignment": assignment,
                    "display_order": display_order,
                }
            )
        return normalized

    def to_representation(self, value):
        return value


class FeatureLinkSerializer(serializers.ModelSerializer):
    """
    Serializer for outgoing FeatureLinks.
    Shows target info for navigation.
    """

    target_type = serializers.SerializerMethodField()

    class Meta:
        model = FeatureLink
        fields = ["id", "target_content_type", "target_object_id", "target_type", "link_type"]
        read_only_fields = fields

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

    hero_image_url = serializers.SerializerMethodField()

    class Meta:
        model = GeoStory
        fields = [
            "id",
            "title",
            "summary",
            "hero_image_url",
            "hero_image_alt",
            "campaign",
            "created_at",
        ]
        read_only_fields = fields

    def get_hero_image_url(self, obj) -> str | None:
        return _absolute_hero_image_url(obj, self.context.get("request"))


class GeoStoryDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for GeoStory detail view.
    Includes owned story content, layers, and feature links.
    """

    layers = serializers.SerializerMethodField()
    feature_links = serializers.SerializerMethodField()
    hero_image_url = serializers.SerializerMethodField()
    content = serializers.SerializerMethodField()

    class Meta:
        model = GeoStory
        fields = [
            "id",
            "title",
            "summary",
            "content",
            "hero_image_url",
            "hero_image_alt",
            "status",
            "campaign",
            "layers",
            "feature_links",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_hero_image_url(self, obj) -> str | None:
        return _absolute_hero_image_url(obj, self.context.get("request"))

    def get_content(self, obj) -> dict:
        return render_content_media_urls(obj.content, self.context.get("request"))

    def get_layers(self, obj) -> list:
        """
        Return layers ordered by display_order.
        Uses the through model to get ordering.
        """
        through_qs = GeoStoryLayer.objects.filter(geostory=obj).select_related(
            "layer__workspace", "style_assignment__style__workspace"
        )
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


class GeoStoryWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for GeoStory model.
    Used for create/update operations (Admin/Editor use).

    Accepts an optional ``layers`` list of layer UUIDs or objects containing
    ``layer``, optional ``style_assignment``, and optional ``display_order``.
    Bare UUIDs remain supported and pin the layer's active default style.
    """

    layers = GeoStoryLayerListField(required=False, write_only=True)

    class Meta:
        model = GeoStory
        fields = [
            "id",
            "title",
            "summary",
            "content",
            "hero_image",
            "hero_image_alt",
            "status",
            "campaign",
            "author",
            "layers",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "author", "created_at", "updated_at"]

    def validate(self, attrs):
        """Invoke model clean() for DB-level validation."""
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

        # Route any incoming hero image upload through the hero tier of the
        # shared image policy before model-level checks run. Validation is
        # read-only — the underlying file bytes are not mutated.
        hero_image = attrs.get("hero_image")
        if hero_image is not None and hasattr(hero_image, "read"):
            try:
                validate_hero_image(hero_image)
            except DjangoValidationError as exc:
                detail = (
                    exc.message_dict
                    if hasattr(exc, "message_dict")
                    else {"hero_image": exc.messages}
                )
                raise serializers.ValidationError(
                    {"hero_image": detail.get("image", detail)}
                ) from exc

        layer_attrs = {k: v for k, v in attrs.items() if k != "layers"}
        if self.instance:
            instance = copy.copy(self.instance)
            for attr, value in layer_attrs.items():
                setattr(instance, attr, value)
        else:
            instance = GeoStory(**layer_attrs)

        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        layers = validated_data.pop("layers", None)
        story = super().create(validated_data)
        if layers is not None:
            self._sync_layers(story, layers)
        return story

    @transaction.atomic
    def update(self, instance, validated_data):
        layers = validated_data.pop("layers", None)
        story = super().update(instance, validated_data)
        if layers is not None:
            self._sync_layers(story, layers)
        return story

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["content"] = render_content_media_urls(instance.content, self.context.get("request"))
        return data

    @staticmethod
    def _sync_layers(story: GeoStory, layers: list) -> None:
        """Replace the story's GeoStoryLayer rows with the supplied list."""
        GeoStoryLayer.objects.filter(geostory=story).delete()
        for item in layers:
            GeoStoryLayer.objects.create(geostory=story, **item)
