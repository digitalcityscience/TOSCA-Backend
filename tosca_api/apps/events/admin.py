from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import (
    Event,
    EventLayer,
    EventSeries,
    EventTerm,
    EventType,
    TaxonomyDimension,
    TaxonomyTerm,
)


class EventLayerInline(admin.TabularInline):
    model = EventLayer
    extra = 1
    autocomplete_fields = ["layer"]


class TaxonomyTermInline(admin.TabularInline):
    model = TaxonomyTerm
    extra = 0
    fields = ["code", "label", "parent", "is_active", "sort_order"]
    autocomplete_fields = ["parent"]


@admin.register(EventType)
class EventTypeAdmin(admin.ModelAdmin):
    list_display = ["label", "code", "profile_mode", "profile_key", "is_active"]
    list_filter = ["profile_mode", "is_active"]
    search_fields = ["label", "code", "profile_key"]
    readonly_fields = ["id", "created_at", "updated_at"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "code",
                    "label",
                    "profile_mode",
                    "profile_key",
                    "is_active",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(TaxonomyDimension)
class TaxonomyDimensionAdmin(admin.ModelAdmin):
    list_display = ["label", "code", "selection_mode", "is_active", "sort_order"]
    list_filter = ["selection_mode", "is_active"]
    search_fields = ["label", "code", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]
    inlines = [TaxonomyTermInline]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "code",
                    "label",
                    "description",
                    "selection_mode",
                    "is_active",
                    "sort_order",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(TaxonomyTerm)
class TaxonomyTermAdmin(admin.ModelAdmin):
    list_display = ["label", "code", "dimension", "parent", "is_active", "sort_order"]
    list_filter = ["dimension", "is_active"]
    search_fields = ["label", "code", "description", "dimension__label", "parent__label"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["dimension", "parent"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "dimension",
                    "parent",
                    "code",
                    "label",
                    "description",
                    "is_active",
                    "sort_order",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(EventSeries)
class EventSeriesAdmin(admin.ModelAdmin):
    list_display = ["name", "campaign", "event_type", "default_context", "created_at"]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["campaign", "event_type", "default_context"]
    fieldsets = (
        (None, {"fields": ("id", "campaign", "event_type", "name", "default_context")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Event)
class EventAdmin(GISModelAdmin):
    """Admin interface for Event with map widget for location."""

    list_display = [
        "title",
        "campaign",
        "event_type",
        "start_datetime",
        "end_datetime",
        "location_mode",
        "status",
        "visibility",
        "organizer",
    ]
    list_filter = ["campaign", "event_type", "location_mode", "status", "visibility", "start_datetime"]
    search_fields = ["title", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["campaign", "organizer", "context", "event_type", "series"]
    inlines = [EventLayerInline]
    date_hierarchy = "start_datetime"

    fieldsets = (
        (None, {"fields": ("id", "campaign", "event_type", "series", "title", "description")}),
        ("Schedule", {"fields": ("start_datetime", "end_datetime")}),
        (
            "Delivery",
            {
                "fields": (
                    "location_mode",
                    "location",
                    "online_url",
                    "online_platform",
                    "access_notes",
                )
            },
        ),
        ("Provider", {"fields": ("provider_name", "provider_url", "provider_contact")}),
        ("Series Metadata", {"fields": ("occurrence_index", "is_exception", "original_start_datetime")}),
        ("Content", {"fields": ("context",)}),
        ("Settings", {"fields": ("status", "visibility", "organizer")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "campaign",
            "event_type",
            "series",
            "organizer",
        )


@admin.register(EventTerm)
class EventTermAdmin(admin.ModelAdmin):
    list_display = ["event", "term", "created_at"]
    list_filter = ["term__dimension"]
    search_fields = ["event__title", "term__label", "term__code", "term__dimension__label"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["event", "term"]
