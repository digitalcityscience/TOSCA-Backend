"""Admin for the external catalog (external catalog ticket 02).

All three admins are row-scoped to the caller's organization through
:class:`OrgScopedAdminMixin` (gate C); capability (which models/actions) is
``has_perm()``'s job, as everywhere else.

Organization and ``created_by`` are derived, never chosen by org-scoped
staff. The organization is resolved while the parent form is *validated*
(not in ``save_model``): Django validates the category's inline items right
after the parent form, and an item's "service belongs to the category's
organization" rule needs the category's organization at that point.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.db.models import Count

from tosca_api.apps.organizations.permissions import (
    OrgScopedAdminMixin,
    get_request_org_context,
    resolve_write_organization,
)

from .models import Category, CategoryItem, ExternalService

VISIBILITY_NOTE = (
    "Visibility and the switches below control what TOSCA lists and offers. The "
    "external data itself stays publicly reachable at the service."
)


class OrganizationOwnedAdminMixin(OrgScopedAdminMixin):
    """Org-scoped admin whose rows carry a direct ``organization`` FK."""

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            # Org-scoped staff only ever see/create rows of their own
            # organization (see get_queryset); the field is derived.
            readonly.append("organization")
        return readonly

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change=change, **kwargs)

        class OrganizationResolvingForm(form_class):
            def clean(self):
                cleaned_data = super().clean()
                if "organization" not in self.fields and not self.instance.organization_id:
                    organization = resolve_write_organization(request)
                    if organization is None:
                        raise forms.ValidationError(
                            "Could not determine an organization for this entry."
                        )
                    self.instance.organization = organization
                return cleaned_data

        return OrganizationResolvingForm

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ExternalService)
class ExternalServiceAdmin(OrganizationOwnedAdminMixin, admin.ModelAdmin):
    list_display = (
        "title",
        "service_type",
        "organization",
        "visibility",
        "is_active",
        "show_uncurated",
        "updated_at",
    )
    list_filter = ("service_type", "visibility", "is_active", "organization")
    search_fields = ("title", "name", "slug", "base_url")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("id", "created_by", "created_at", "updated_at")
    ordering = ("title",)

    fieldsets = (
        (None, {"fields": ("id", "organization", "name", "slug", "title")}),
        (
            "Connection",
            {
                "fields": ("service_type", "base_url", "mqtt_url"),
                "description": "The MQTT URL (wss://…) applies to SensorThings services only.",
            },
        ),
        ("Description", {"fields": ("attribution", "description")}),
        (
            "Visibility",
            {"fields": ("visibility", "is_active"), "description": VISIBILITY_NOTE},
        ),
        (
            "What users may do",
            {
                "fields": (
                    "show_uncurated",
                    "allow_full_load",
                    "allow_live_updates",
                    "allow_server_filters",
                    "max_features",
                ),
                "description": VISIBILITY_NOTE,
            },
        ),
        ("Ownership", {"fields": ("created_by",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


class CategoryItemInlineForm(forms.ModelForm):
    class Meta:
        model = CategoryItem
        fields = "__all__"
        widgets = {
            "ogc_collection_ids": forms.Textarea(attrs={"rows": 2}),
            "default_properties": forms.Textarea(attrs={"rows": 2}),
            "default_filter": forms.Textarea(attrs={"rows": 3}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "color": forms.TextInput(attrs={"type": "color"}),
        }

    def _list_value(self, field):
        # An empty JSON textarea submits None; these fields are lists.
        value = self.cleaned_data.get(field)
        return [] if value is None else value

    def clean_ogc_collection_ids(self):
        return self._list_value("ogc_collection_ids")

    def clean_default_properties(self):
        return self._list_value("default_properties")

    def clean_default_filter(self):
        return self._list_value("default_filter")


class CategoryItemInline(admin.StackedInline):
    model = CategoryItem
    form = CategoryItemInlineForm
    extra = 0
    ordering = ("display_order", "title")
    readonly_fields = ("availability_state", "feature_count", "last_checked_at", "last_check_error")
    fieldsets = (
        (None, {"fields": ("service", "title", "display_order", "description")}),
        ("Map", {"fields": ("color", "min_zoom")}),
        (
            "OGC API Features",
            {
                "fields": ("ogc_dataset_id", "ogc_collection_ids", "default_properties", "default_filter"),
                "description": (
                    "Only for OGC API services. Collection ids as a JSON list, e.g. "
                    '["stadtrad_stationen"]; several ids are shown as one merged layer. '
                    'Default filter: [{"property": "breite", "operator": "gte", "value": 5}].'
                ),
            },
        ),
        (
            "SensorThings",
            {
                "fields": ("sta_service_name", "sta_layer_name"),
                "description": (
                    "Only for SensorThings services: the datastreams' properties.serviceName "
                    "and properties.layerName, e.g. HH_STA_E-Ladestationen / Status_E-Ladepunkt."
                ),
            },
        ),
        (
            "Availability (set by the health check)",
            {
                "fields": ("availability_state", "feature_count", "last_checked_at", "last_check_error"),
                "classes": ("collapse",),
            },
        ),
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "service":
            kwargs["queryset"] = _services_for(request)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


def _services_for(request):
    """Services an item may use: the caller's organization's (all for superusers).

    The model's ``clean`` additionally requires the service and category to
    share an organization, which also covers superusers.
    """
    queryset = ExternalService.objects.select_related("organization").order_by("title")
    if request.user.is_superuser:
        return queryset
    _roles, org_slug, exempt = get_request_org_context(request)
    if exempt:
        return queryset
    if not org_slug:
        return queryset.none()
    return queryset.filter(organization__slug=org_slug)


@admin.register(Category)
class CategoryAdmin(OrganizationOwnedAdminMixin, admin.ModelAdmin):
    inlines = (CategoryItemInline,)
    list_display = ("title", "organization", "display_order", "visibility", "is_active", "item_count")
    list_filter = ("visibility", "is_active", "organization")
    search_fields = ("title", "slug", "description")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("id", "created_by", "created_at", "updated_at")
    ordering = ("display_order", "title")

    fieldsets = (
        (None, {"fields": ("id", "organization", "title", "slug", "description", "display_order")}),
        (
            "Visibility",
            {"fields": ("visibility", "is_active"), "description": VISIBILITY_NOTE},
        ),
        ("Ownership", {"fields": ("created_by",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_item_count=Count("items"))

    @admin.display(description="Items", ordering="_item_count")
    def item_count(self, obj):
        return obj._item_count

    def save_formset(self, request, form, formset, change):
        for instance in formset.save(commit=False):
            if instance._state.adding:
                instance.created_by = request.user
            instance.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


@admin.register(CategoryItem)
class CategoryItemAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    """Read-only overview across categories; items are edited in their category."""

    org_lookup = "category__organization__slug"
    list_display = (
        "title",
        "category",
        "service",
        "source",
        "availability_state",
        "feature_count",
        "last_checked_at",
    )
    list_filter = ("availability_state", "service__service_type", "service", "category")
    search_fields = ("title", "ogc_collection_ids", "sta_service_name", "sta_layer_name")
    list_select_related = ("category", "service")
    ordering = ("category__display_order", "category__title", "display_order", "title")

    @admin.display(description="Source")
    def source(self, obj):
        if obj.service.service_type == ExternalService.ServiceType.SENSORTHINGS:
            return f"{obj.sta_service_name} / {obj.sta_layer_name}"
        collections = ", ".join(obj.ogc_collection_ids)
        return f"{obj.ogc_dataset_id} / {collections}" if obj.ogc_dataset_id else collections

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
