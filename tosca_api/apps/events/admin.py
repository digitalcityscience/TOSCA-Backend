from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import (
    CultureEventProfile,
    Event,
    EventLayer,
    EventSeries,
    EventSeriesDate,
    EventTerm,
    EventType,
    PublicHealthEventProfile,
    SportsEventProfile,
    TaxonomyDimension,
    TaxonomyTerm,
)


class SortOrderHelpTextMixin:
    """Explain that sort_order=0 auto-appends within the current scope."""

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "sort_order":
            suffix = " Leave as 0 to auto-append."
            existing_help_text = formfield.help_text or ""
            formfield.help_text = f"{existing_help_text}{suffix}".strip()
        return formfield


class EventLayerInline(admin.TabularInline):
    model = EventLayer
    extra = 1
    autocomplete_fields = ["layer"]


class EventSeriesDateInline(admin.TabularInline):
    model = EventSeriesDate
    extra = 0
    fields = ["occurrence_date", "display_order"]


class TaxonomyTermInline(SortOrderHelpTextMixin, admin.TabularInline):
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
class TaxonomyDimensionAdmin(SortOrderHelpTextMixin, admin.ModelAdmin):
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
class TaxonomyTermAdmin(SortOrderHelpTextMixin, admin.ModelAdmin):
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
    list_display = [
        "name",
        "series_mode",
        "campaign",
        "event_type",
        "start_date",
        "created_at",
    ]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["campaign", "event_type", "default_context", "created_by"]
    inlines = [EventSeriesDateInline]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "campaign",
                    "event_type",
                    "created_by",
                    "name",
                    "default_context",
                    "series_mode",
                )
            },
        ),
        (
            "Recurrence",
            {
                "fields": (
                    "recurrence_type",
                    "start_date",
                    "end_date",
                    "occurrence_count",
                    "interval",
                    "start_time",
                    "end_time",
                    "timezone",
                    "by_weekday",
                    "monthly_rule_type",
                    "day_of_month",
                    "week_of_month",
                    "weekday_of_month",
                    "notes",
                )
            },
        ),
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


class EventProfileAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at", "updated_at"]
    autocomplete_fields = ["event"]
    search_fields = ["event__title"]


@admin.register(PublicHealthEventProfile)
class PublicHealthEventProfileAdmin(EventProfileAdmin):
    list_display = ["event", "insurance_eligible", "referral_required", "created_at"]


@admin.register(SportsEventProfile)
class SportsEventProfileAdmin(EventProfileAdmin):
    list_display = ["event", "sport_name", "skill_level", "created_at"]


@admin.register(CultureEventProfile)
class CultureEventProfileAdmin(EventProfileAdmin):
    list_display = ["event", "format_label", "age_rating", "created_at"]
