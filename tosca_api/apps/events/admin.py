from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.db import transaction

from .forms import (
    EventAdminForm,
    EventSeriesAdminForm,
    TaxonomyDimensionAdminForm,
    build_taxonomy_dimension_form_fields,
    get_taxonomy_dimensions_for_source,
    taxonomy_dimension_field_name,
)
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
from .services import (
    get_base_template_event,
    orchestrate_series_create,
    orchestrate_series_update,
)


def build_admin_taxonomy_form_class(
    form_class,
    *,
    source_event=None,
    include_all_profile_dimensions: bool = False,
    class_name: str,
):
    """Return a form subclass with taxonomy fields declared at class creation time."""
    _, _, taxonomy_form_fields, _, _ = build_taxonomy_dimension_form_fields(
        source_event,
        include_all_profile_dimensions=include_all_profile_dimensions,
    )
    if not taxonomy_form_fields:
        return form_class
    return type(class_name, (form_class,), taxonomy_form_fields)


def taxonomy_dimension_fieldset_classes(dimensions):
    classes = ["events-taxonomy-section"]
    profile_keys = {
        dimension.profile_key
        for dimension in dimensions
        if dimension.profile_key
    }
    classes.extend(
        f"events-taxonomy-{profile_key.replace('_', '-')}"
        for profile_key in sorted(profile_keys)
    )
    if any(not dimension.profile_key for dimension in dimensions):
        classes.append("events-taxonomy-unscoped")
    return tuple(classes)


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
    form = TaxonomyDimensionAdminForm
    list_display = [
        "label",
        "code",
        "selection_mode",
        "profile_key",
        "is_active",
        "sort_order",
    ]
    list_filter = ["selection_mode", "profile_key", "is_active"]
    search_fields = ["label", "code", "description", "profile_key"]
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
                    "profile_key",
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
        base_event = get_base_template_event(obj) if obj else None
        include_all_profile_dimensions = base_event is None
        kwargs["form"] = build_admin_taxonomy_form_class(
            self.form,
            source_event=base_event,
            include_all_profile_dimensions=include_all_profile_dimensions,
            class_name="DynamicEventSeriesAdminForm",
        )
        base_form = super().get_form(request, obj, change=change, **kwargs)

        class RequestAwareEventSeriesAdminForm(base_form):
            def __init__(self, *args, **inner_kwargs):
                inner_kwargs.setdefault("created_by_user", request.user)
                super().__init__(*args, **inner_kwargs)

        return RequestAwareEventSeriesAdminForm

    def get_fieldsets(self, request, obj=None):
        base_event = get_base_template_event(obj) if obj else None
        include_all_profile_dimensions = base_event is None
        taxonomy_dimensions = get_taxonomy_dimensions_for_source(
            base_event,
            include_all_profile_dimensions=include_all_profile_dimensions,
        )
        taxonomy_fields = tuple(
            taxonomy_dimension_field_name(dimension)
            for dimension in taxonomy_dimensions
        )
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
            (
                "Event Template",
                {
                    "description": (
                        "These fields define the event that will be generated "
                        "for each occurrence in the series."
                    ),
                    "fields": (
                        "title",
                        "summary",
                        "lead_name",
                        "language",
                        "language_note",
                        "external_url",
                        "status",
                        "visibility",
                    ),
                },
            ),
            (
                "Event Delivery",
                {
                    "classes": ("events-location-section",),
                    "fields": (
                        "location_mode",
                        "location",
                        "venue_address",
                        "district",
                        "online_url",
                        "online_platform",
                        "access_notes",
                    ),
                },
            ),
            (
                "Event Provider",
                {
                    "fields": (
                        "provider_name",
                        "provider_address",
                        "provider_phone",
                        "provider_email",
                        "provider_social",
                        "provider_url",
                    ),
                },
            ),
            (
                "Public Health Details",
                {
                    "classes": ("events-profile-section", "events-profile-public_health"),
                    "fields": (
                        "public_health_insurance_eligible",
                        "public_health_referral_required",
                        "public_health_target_age_note",
                        "public_health_registration",
                        "public_health_short_notice_possible",
                        "public_health_cost_amount_eur",
                        "public_health_reduced_amount_eur",
                        "public_health_subsidy_program",
                        "public_health_transit_note",
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
            (
                "Content & Taxonomy",
                {
                    "classes": taxonomy_dimension_fieldset_classes(taxonomy_dimensions),
                    "fields": (
                        "context",
                        *taxonomy_fields,
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

    def save_related(self, request, form, formsets, change):
        """Run occurrence generation/sync after inline dates are saved."""
        super().save_related(request, form, formsets, change)

        series = form.instance
        template_data = getattr(form, "_template_data", None)
        taxonomy_terms = getattr(form, "_taxonomy_terms", [])
        profile_data = getattr(form, "_profile_data", None)

        if not template_data:
            return

        with transaction.atomic():
            if not change:
                orchestrate_series_create(
                    series=series,
                    event_template=template_data,
                    organizer=request.user,
                    taxonomy_terms=taxonomy_terms,
                    profile_data=profile_data,
                )
            else:
                orchestrate_series_update(
                    series=series,
                    event_template=template_data,
                    organizer=request.user,
                    taxonomy_terms=taxonomy_terms,
                    profile_data=profile_data,
                )

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
    search_fields = ["title", "summary"]
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

    def get_form(self, request, obj=None, change=False, **kwargs):
        source_event = obj if obj and obj.pk else None
        include_all_profile_dimensions = source_event is None
        kwargs["form"] = build_admin_taxonomy_form_class(
            self.form,
            source_event=source_event,
            include_all_profile_dimensions=include_all_profile_dimensions,
            class_name="DynamicEventAdminForm",
        )
        return super().get_form(request, obj, change=change, **kwargs)

    def get_fieldsets(self, request, obj=None):
        source_event = obj if obj and obj.pk else None
        include_all_profile_dimensions = source_event is None
        taxonomy_dimensions = get_taxonomy_dimensions_for_source(
            source_event,
            include_all_profile_dimensions=include_all_profile_dimensions,
        )
        taxonomy_fields = tuple(
            taxonomy_dimension_field_name(dimension)
            for dimension in taxonomy_dimensions
        )
        fieldsets = [
            (
                None,
                {
                    "fields": (
                        "campaign",
                        "event_type",
                        "series",
                        "title",
                        "summary",
                        "lead_name",
                        "language",
                        "language_note",
                        "external_url",
                    )
                },
            ),
            ("Schedule", {"fields": ("start_datetime", "end_datetime")}),
            (
                "Delivery",
                {
                    "classes": ("events-location-section",),
                    "fields": (
                        "location_mode",
                        "location",
                        "venue_address",
                        "district",
                        "online_url",
                        "online_platform",
                        "access_notes",
                    ),
                },
            ),
            (
                "Provider",
                {
                    "fields": (
                        "provider_name",
                        "provider_address",
                        "provider_phone",
                        "provider_email",
                        "provider_social",
                        "provider_url",
                    )
                },
            ),
            (
                "Public Health Details",
                {
                    "classes": ("events-profile-section", "events-profile-public_health"),
                    "fields": (
                        "public_health_insurance_eligible",
                        "public_health_referral_required",
                        "public_health_target_age_note",
                        "public_health_registration",
                        "public_health_short_notice_possible",
                        "public_health_cost_amount_eur",
                        "public_health_reduced_amount_eur",
                        "public_health_subsidy_program",
                        "public_health_transit_note",
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
        if taxonomy_fields:
            fieldsets.insert(
                -1,
                (
                    "Taxonomy",
                    {
                        "classes": taxonomy_dimension_fieldset_classes(taxonomy_dimensions),
                        "fields": taxonomy_fields,
                    },
                ),
            )

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
        form.save_taxonomy(obj)

    class Media:
        js = ("events/js/admin_events.js",)


@admin.register(EventTerm)
class EventTermAdmin(admin.ModelAdmin):
    list_display = ["event", "term", "created_at"]
    list_filter = ["term__dimension"]
    search_fields = ["event__title", "term__label", "term__code", "term__dimension__label"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["event", "term"]
