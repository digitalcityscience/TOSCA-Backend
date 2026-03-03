from django.contrib import admin

from .models import FeedbackLayer, GeoFeedback


class FeedbackLayerInline(admin.TabularInline):
    model = FeedbackLayer
    extra = 1
    autocomplete_fields = ["layer"]


@admin.register(GeoFeedback)
class GeoFeedbackAdmin(admin.ModelAdmin):
    """Admin interface for GeoFeedback."""

    list_display = [
        "title",
        "campaign",
        "status",
        "visibility",
        "rating_enabled",
        "form_enabled",
        "allow_drawings",
        "created_by",
        "created_at",
    ]
    list_filter = ["campaign", "status", "visibility", "rating_enabled", "form_enabled"]
    search_fields = ["title", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["campaign", "created_by", "context", "custom_form"]
    inlines = [FeedbackLayerInline]

    fieldsets = (
        (None, {"fields": ("id", "campaign", "title", "description")}),
        ("Content", {"fields": ("context",)}),
        (
            "Feedback Configuration",
            {
                "fields": (
                    "custom_form",
                    "rating_enabled",
                    "form_enabled",
                    "allow_drawings",
                ),
            },
        ),
        ("Settings", {"fields": ("status", "visibility", "created_by")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "campaign", "created_by", "custom_form"
        )
