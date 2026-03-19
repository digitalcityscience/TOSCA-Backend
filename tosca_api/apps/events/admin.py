from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import Event, EventLayer, EventSeries, EventType


class EventLayerInline(admin.TabularInline):
    model = EventLayer
    extra = 1
    autocomplete_fields = ["layer"]


@admin.register(EventType)
class EventTypeAdmin(admin.ModelAdmin):
    list_display = ["label", "code"]
    search_fields = ["label", "code"]


@admin.register(EventSeries)
class EventSeriesAdmin(admin.ModelAdmin):
    list_display = ["name", "created_at"]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]


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
