from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .forms import FeedbackLayerFormSet
from .models import FeedbackLayer, FeedbackSubmission, GeoFeedback


class FeedbackLayerInline(admin.TabularInline):
    model = FeedbackLayer
    formset = FeedbackLayerFormSet
    extra = 1
    autocomplete_fields = ["layer"]

    class Media:
        js = ("feedback/js/admin_feedback.js",)


class FeedbackSubmissionInline(admin.TabularInline):
    model = FeedbackSubmission
    extra = 0
    readonly_fields = ["id", "submitted_by", "rating", "form_data", "created_at"]
    fields = ["id", "submitted_by", "rating", "form_data", "is_anonymized", "created_at"]
    show_change_link = True


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
    inlines = [FeedbackLayerInline, FeedbackSubmissionInline]

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


@admin.register(FeedbackSubmission)
class FeedbackSubmissionAdmin(GISModelAdmin):
    """Admin interface for FeedbackSubmission with map widget for geometry."""

    list_display = [
        "id",
        "feedback",
        "submitted_by",
        "rating",
        "is_anonymized",
        "created_at",
    ]
    list_filter = ["feedback", "is_anonymized", "rating"]
    search_fields = ["feedback__title"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["feedback", "submitted_by"]

    fieldsets = (
        (None, {"fields": ("id", "feedback", "submitted_by")}),
        ("Response", {"fields": ("rating", "form_data")}),
        ("Spatial Data", {"fields": ("geometry",)}),
        ("Settings", {"fields": ("is_anonymized",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "feedback", "submitted_by"
        )
