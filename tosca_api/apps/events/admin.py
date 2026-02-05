from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import CalendarEvent, EventLayer


class EventLayerInline(admin.TabularInline):
    model = EventLayer
    extra = 1
    autocomplete_fields = ["layer"]


@admin.register(CalendarEvent)
class CalendarEventAdmin(GISModelAdmin):
    """Admin interface for CalendarEvent with map widget for location."""

    list_display = [
        "title",
        "campaign",
        "start_datetime",
        "end_datetime",
        "status",
        "visibility",
        "organizer",
    ]
    list_filter = ["campaign", "status", "visibility", "start_datetime"]
    search_fields = ["title", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["campaign", "organizer", "context"]
    inlines = [EventLayerInline]
    date_hierarchy = "start_datetime"

    fieldsets = (
        (None, {"fields": ("id", "campaign", "title", "description")}),
        ("Schedule", {"fields": ("start_datetime", "end_datetime")}),
        ("Location", {"fields": ("location",)}),
        ("Content", {"fields": ("context",)}),
        ("Settings", {"fields": ("status", "visibility", "organizer")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("campaign", "organizer")
