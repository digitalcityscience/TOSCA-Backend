from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .forms import EventAdminForm, EventSeriesAdminForm
from .models import (
    Event,
    EventLayer,
    EventSeries,
    EventSeriesDate,
    EventTerm,
    EventType,
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
    extra = 1
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
    form = EventSeriesAdminForm
    list_display = [
        "name",
        "series_mode",
        "campaign",
        "event_type",
        "start_date",
        "created_at",
    ]
    search_fields = ["name"]
    readonly_fields = ["id", "created_by", "created_at", "updated_at"]
    autocomplete_fields = ["campaign", "default_context"]
    inlines = [EventSeriesDateInline]

    def get_form(self, request, obj=None, change=False, **kwargs):
        base_form = super().get_form(request, obj, change=change, **kwargs)

        class RequestAwareEventSeriesAdminForm(base_form):
            def __init__(self, *args, **inner_kwargs):
                inner_kwargs.setdefault("created_by_user", request.user)
                super().__init__(*args, **inner_kwargs)

        return RequestAwareEventSeriesAdminForm

    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            (
                None,
                {
                    "fields": (
                        "campaign",
                        "event_type",
                        "name",
                        "default_context",
                        "series_mode",
                    )
                },
            ),
            (
                "Recurrence",
                {
                    "classes": ("events-series-recurrence",),
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
                    ),
                },
            ),
        ]
        if obj and not obj._state.adding:
            fieldsets.append(("Metadata", {"fields": ("id", "created_by")}))
            fieldsets.append(("Timestamps", {"fields": ("created_at", "updated_at")}))
        return fieldsets

    def save_model(self, request, obj, form, change):
        if obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    class Media:
        js = ("events/js/admin_events.js",)


@admin.register(Event)
class EventAdmin(GISModelAdmin):
    """Admin interface for Event with map widget for location."""

    form = EventAdminForm
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
    readonly_fields = [
        "id",
        "occurrence_index",
        "is_exception",
        "original_start_datetime",
        "created_at",
        "updated_at",
    ]
    autocomplete_fields = ["campaign", "organizer", "context", "series"]
    inlines = [EventLayerInline]
    date_hierarchy = "start_datetime"

    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            (None, {"fields": ("campaign", "event_type", "series", "title", "description")}),
            ("Schedule", {"fields": ("start_datetime", "end_datetime")}),
            (
                "Delivery",
                {
                    "classes": ("events-location-section",),
                    "fields": (
                        "location_mode",
                        "location",
                        "online_url",
                        "online_platform",
                        "access_notes",
                    ),
                },
            ),
            ("Provider", {"fields": ("provider_name", "provider_url", "provider_contact")}),
            (
                "Public Health Details",
                {
                    "classes": ("events-profile-section", "events-profile-public_health"),
                    "fields": (
                        "public_health_insurance_eligible",
                        "public_health_referral_required",
                    ),
                },
            ),
            (
                "Sports Details",
                {
                    "classes": ("events-profile-section", "events-profile-sports"),
                    "fields": (
                        "sports_sport_name",
                        "sports_skill_level",
                    ),
                },
            ),
            (
                "Culture Details",
                {
                    "classes": ("events-profile-section", "events-profile-culture"),
                    "fields": (
                        "culture_format_label",
                        "culture_age_rating",
                    ),
                },
            ),
            ("Content", {"fields": ("context",)}),
            ("Settings", {"fields": ("status", "visibility", "organizer")}),
        ]

        if obj and not obj._state.adding:
            fieldsets.append(
                (
                    "Series Metadata",
                    {"fields": ("occurrence_index", "is_exception", "original_start_datetime")},
                )
            )
            fieldsets.append(("Timestamps", {"fields": ("id", "created_at", "updated_at")}))

        return fieldsets

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "campaign",
            "event_type",
            "series",
            "organizer",
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        form.save_profile(obj)

    class Media:
        js = ("events/js/admin_events.js",)


@admin.register(EventTerm)
class EventTermAdmin(admin.ModelAdmin):
    list_display = ["event", "term", "created_at"]
    list_filter = ["term__dimension"]
    search_fields = ["event__title", "term__label", "term__code", "term__dimension__label"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["event", "term"]
