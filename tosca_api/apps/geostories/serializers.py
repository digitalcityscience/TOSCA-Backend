import copy

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from tosca_api.apps.core.image_policy import validate_hero_image
from tosca_api.apps.core.editorjs import render_content_media_urls
from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.geodata_providers.api.serializers import (
    LayerSummarySerializer,
)

from .models import GeoStory, GeoStoryScene, GeoStorySceneLayer
from .scene_manifest import (
    build_scene_legend,
    build_scene_render_layers,
    build_story_map,
    source_key,
    visible_scene_layers,
)


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


def _style_assignment_payload(assignment) -> dict | None:
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


class SceneLayerSummarySerializer(LayerSummarySerializer):
    """Layer summary plus its provider, for provider-scoped catalog and legend lookups."""

    provider = serializers.SerializerMethodField()

    class Meta(LayerSummarySerializer.Meta):
        fields = [*LayerSummarySerializer.Meta.fields, "provider"]
        read_only_fields = fields

    def get_provider(self, obj) -> dict:
        engine = obj.workspace.geodata_engine
        return {
            "id": str(engine.id),
            "name": engine.name,
            "base_url": engine.public_url.rstrip("/"),
        }


class GeoStorySceneLayerSerializer(serializers.ModelSerializer):
    """A layer rendered in a scene, with its pinned style and feature selection."""

    layer = SceneLayerSummarySerializer(read_only=True)
    style_assignment = serializers.SerializerMethodField()
    render_layer_ids = serializers.SerializerMethodField()
    source_key = serializers.SerializerMethodField()
    features = serializers.SerializerMethodField()

    class Meta:
        model = GeoStorySceneLayer
        fields = [
            "id",
            "layer",
            "style_assignment",
            "render_layer_ids",
            "source_key",
            "display_order",
            "opacity",
            "features",
        ]
        read_only_fields = fields

    def get_style_assignment(self, obj) -> dict | None:
        return _style_assignment_payload(obj.style_assignment)

    def get_render_layer_ids(self, obj) -> list[str]:
        return obj.effective_style_layer_ids

    def get_source_key(self, obj) -> str | None:
        """Key into the story's ``map.sources``; null when the layer has no style."""
        return source_key(obj) if obj.style_assignment_id else None

    def get_features(self, obj) -> dict:
        return {
            "mode": obj.feature_mode,
            "attribute": obj.feature_id_attribute or None,
            "ids": obj.feature_ids,
        }


class GeoStorySceneSerializer(serializers.ModelSerializer):
    """A captured map state; ``camera.center`` is null when the map fits its layers."""

    camera = serializers.SerializerMethodField()
    transition = serializers.SerializerMethodField()
    layers = serializers.SerializerMethodField()
    render_layers = serializers.SerializerMethodField()
    legend = serializers.SerializerMethodField()

    class Meta:
        model = GeoStoryScene
        fields = [
            "id",
            "order",
            "title",
            "caption",
            "camera",
            "transition",
            "layers",
            "render_layers",
            "legend",
        ]
        read_only_fields = fields

    def get_camera(self, obj) -> dict:
        has_center = obj.center_lng is not None and obj.center_lat is not None
        return {
            "center": [obj.center_lng, obj.center_lat] if has_center else None,
            "zoom": obj.zoom,
            "bearing": obj.bearing,
            "pitch": obj.pitch,
            "bounds": obj.bounds,
        }

    def get_transition(self, obj) -> dict:
        return {"type": obj.transition, "duration_ms": obj.duration_ms}

    def get_layers(self, obj) -> list:
        return GeoStorySceneLayerSerializer(visible_scene_layers(obj), many=True).data

    def get_render_layers(self, obj) -> list:
        """MapLibre layers for this scene, bottom to top, over the story's ``map.sources``."""
        return build_scene_render_layers(obj)

    def get_legend(self, obj) -> list:
        return build_scene_legend(obj)


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
            "about_author",
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
    Includes owned story content, scenes, and feature links.

    ``layers`` is deprecated: it lists the distinct layer/style pairs across
    all scenes for clients that predate scenes, and is removed in Task 10.9.
    """

    scenes = serializers.SerializerMethodField()
    map = serializers.SerializerMethodField()
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
            "about_author",
            "content",
            "hero_image_url",
            "hero_image_alt",
            "status",
            "campaign",
            "scenes",
            "map",
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

    def get_scenes(self, obj) -> list:
        return GeoStorySceneSerializer(obj.scenes.all(), many=True).data

    def get_map(self, obj) -> dict:
        """Sources, styles and sprites shared by every scene's ``render_layers``."""
        return build_story_map(request=self.context.get("request"), scenes=obj.scenes.all())

    def get_layers(self, obj) -> list:
        layers = []
        seen = set()
        for scene in obj.scenes.all():
            for scene_layer in visible_scene_layers(scene):
                key = (scene_layer.layer_id, scene_layer.style_assignment_id)
                if key in seen:
                    continue
                seen.add(key)
                layers.append(
                    {
                        "layer": LayerSummarySerializer(scene_layer.layer).data,
                        "style_assignment": _style_assignment_payload(
                            scene_layer.style_assignment
                        ),
                        "display_order": len(layers),
                    }
                )
        return layers

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

    Map scenes are authored in the Django admin only and are not writable here.
    """

    class Meta:
        model = GeoStory
        fields = [
            "id",
            "title",
            "summary",
            "about_author",
            "content",
            "hero_image",
            "hero_image_alt",
            "status",
            "campaign",
            "author",
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

        if self.instance:
            instance = copy.copy(self.instance)
            for attr, value in attrs.items():
                setattr(instance, attr, value)
        else:
            instance = GeoStory(**attrs)

        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["content"] = render_content_media_urls(instance.content, self.context.get("request"))
        return data
