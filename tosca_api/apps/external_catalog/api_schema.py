"""OpenAPI shapes of the external catalog API (ticket 06).

Documentation only: the views build their payloads in ``api.py``;
``tests/test_api_docs.py`` checks that real payloads use exactly these keys.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import CategoryItem, ExternalService


class ExternalServiceCapabilitiesSchema(serializers.Serializer):
    show_uncurated = serializers.BooleanField(help_text="Offer an 'All datasets' list of uncurated data.")
    full_load = serializers.BooleanField(help_text="Allow loading all features at once (OGC).")
    live_updates = serializers.BooleanField(help_text="Allow MQTT live updates (SensorThings).")
    server_filters = serializers.BooleanField(help_text="Allow attribute selection and server filters (OGC).")
    max_features = serializers.IntegerField(allow_null=True, help_text="Per-load feature cap; null = app default.")


class ExternalServiceSchema(serializers.Serializer):
    id = serializers.UUIDField()
    slug = serializers.SlugField(help_text="Stable id; items reference services by slug.")
    service_type = serializers.ChoiceField(choices=ExternalService.ServiceType.choices)
    title = serializers.CharField()
    base_url = serializers.URLField(help_text="Fetch features/observations directly from here.")
    attribution = serializers.CharField(allow_blank=True)
    capabilities = ExternalServiceCapabilitiesSchema()
    mqtt_url = serializers.CharField(
        required=False, allow_blank=True, help_text="SensorThings services only; may be empty."
    )


class ExternalCategorySummarySchema(serializers.Serializer):
    slug = serializers.SlugField()
    title = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    display_order = serializers.IntegerField()
    item_count = serializers.IntegerField(help_text="Items listed to the caller.")


class OgcSourceSchema(serializers.Serializer):
    dataset_id = serializers.CharField(allow_blank=True, help_text="Empty for a single landing page.")
    dataset_title = serializers.CharField(allow_blank=True)
    collection_id = serializers.CharField(help_text="Exactly one collection per item.")


class SensorThingsSourceSchema(serializers.Serializer):
    service_name = serializers.CharField(help_text="Datastream properties.serviceName")
    layer_name = serializers.CharField(help_text="Datastream properties.layerName")


class FilterConditionSchema(serializers.Serializer):
    property = serializers.CharField()
    operator = serializers.ChoiceField(choices=["eq", "neq", "lt", "lte", "gt", "gte", "contains"])
    value = serializers.JSONField(help_text="String, number or boolean.")


class OgcDefaultsSchema(serializers.Serializer):
    properties = serializers.ListField(child=serializers.CharField(), help_text="Empty = all attributes.")
    filter = FilterConditionSchema(many=True)


class ItemStyleSchema(serializers.Serializer):
    color = serializers.RegexField(r"^#[0-9a-fA-F]{6}$")


class ItemLoadingSchema(serializers.Serializer):
    min_zoom = serializers.FloatField(allow_null=True)


class ItemAvailabilitySchema(serializers.Serializer):
    state = serializers.ChoiceField(
        choices=CategoryItem.AvailabilityState.choices, help_text="UNKNOWN until the first health check."
    )
    feature_count = serializers.IntegerField(
        allow_null=True, help_text="Features (OGC) or datastreams (SensorThings); use instead of $count."
    )
    checked_at = serializers.DateTimeField(allow_null=True)


class ExternalCategoryItemSchema(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField(help_text="From the service.")
    description = serializers.CharField(allow_blank=True, help_text="From the service; may be long or empty.")
    service = serializers.SlugField(help_text="Slug of a service in external-services.")
    service_type = serializers.ChoiceField(choices=ExternalService.ServiceType.choices)
    ogc = OgcSourceSchema(required=False, help_text="OGC API items only.")
    defaults = OgcDefaultsSchema(required=False, help_text="OGC API items only: initial query.")
    sensorthings = SensorThingsSourceSchema(required=False, help_text="SensorThings items only.")
    style = ItemStyleSchema()
    loading = ItemLoadingSchema()
    availability = ItemAvailabilitySchema()


class ExternalCategoryDetailSchema(serializers.Serializer):
    slug = serializers.SlugField()
    title = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    items = ExternalCategoryItemSchema(many=True)


class NotFoundSchema(serializers.Serializer):
    detail = serializers.CharField()
