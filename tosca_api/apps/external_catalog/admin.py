"""Admin for the external catalog (external catalog tickets 02, 03b).

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

import json

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.formats import date_format
from django.utils.text import slugify

from tosca_api.apps.organizations.permissions import (
    OrgScopedAdminMixin,
    get_request_org_context,
    resolve_write_organization,
)

from . import harvest, remote
from .models import Category, CategoryItem, ExternalService, ServiceSource, Visibility

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
    readonly_fields = ("id", "created_by", "created_at", "updated_at", "catalog_status")
    ordering = ("title",)
    actions = ("load_catalog",)

    fieldsets = (
        (None, {"fields": ("id", "organization", "name", "slug", "title")}),
        (
            "Connection",
            {
                "fields": ("service_type", "base_url", "mqtt_url"),
                "description": "The MQTT URL (wss://…) applies to SensorThings services only.",
            },
        ),
        (
            "Catalog",
            {
                "fields": ("catalog_status",),
                "description": (
                    "Everything the service offers (collections or SensorThings layers, with "
                    "titles, descriptions and themes) is copied into a local index so categories "
                    "can be composed quickly. Load or update it with the button in the category "
                    "picker or the 'Load catalog' action in the service list."
                ),
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

    # ------------------------------------------------------------------
    # Source index (ticket 03b): staff-only JSON for the category picker.
    # ------------------------------------------------------------------
    def get_urls(self):
        urls = [
            path(
                "<path:object_id>/catalog/",
                self.admin_site.admin_view(self._catalog_view),
                name="external_catalog_externalservice_catalog",
            ),
            path(
                "<path:object_id>/sources/",
                self.admin_site.admin_view(self._sources_view),
                name="external_catalog_externalservice_sources",
            ),
        ]
        return urls + super().get_urls()

    def _service_or_404(self, request, object_id) -> ExternalService:
        # get_object filters through the org-scoped queryset: another
        # organization's service is a 404, exactly like its change page.
        service = self.get_object(request, object_id)
        if service is None or not self.has_view_permission(request, service):
            raise Http404("Service not found.")
        return service

    def _catalog_view(self, request, object_id):
        """GET: load state. POST: start loading the catalog in the background."""
        service = self._service_or_404(request, object_id)
        if request.method == "POST":
            if not self.has_change_permission(request, service):
                return JsonResponse({"error": "You may not change this service."}, status=403)
            started = harvest.start_in_background(service)
            service.refresh_from_db()
            return JsonResponse({**harvest.state(service), "started": started}, status=202 if started else 200)
        return JsonResponse(harvest.state(service))

    def _sources_view(self, request, object_id):
        """The service's indexed, map-able sources plus theme filter options.

        Compact rows ``[id, dataset_id, dataset_title, source_id, title,
        count, themes]``: Hamburg has well over a thousand collections.
        """
        service = self._service_or_404(request, object_id)
        rows = list(
            ServiceSource.objects.filter(service=service, geojson=True)
            .order_by("dataset_title", "title", "id")
            .values_list("id", "dataset_id", "dataset_title", "source_id", "title", "count", "themes")
        )
        datasets_per_theme: dict[str, set[str]] = {}
        for _pk, dataset_id, _dataset_title, _source_id, _title, _count, themes in rows:
            for code in themes:
                datasets_per_theme.setdefault(code, set()).add(dataset_id)
        return JsonResponse(
            {
                "catalog": harvest.state(service),
                "themes": [
                    {"code": code, "label": label, "datasets": len(datasets_per_theme[code])}
                    for code, label in sorted(remote.THEMES.items(), key=lambda entry: entry[1])
                    if code in datasets_per_theme
                ],
                "sources": rows,
            }
        )

    @admin.action(description="Load catalog (runs in the background, up to a few minutes)")
    def load_catalog(self, request, queryset):
        started = [service.title for service in queryset if harvest.start_in_background(service)]
        skipped = queryset.count() - len(started)
        if started:
            self.message_user(request, f"Loading the catalog of: {', '.join(started)}. Reload this page later.")
        if skipped:
            self.message_user(request, f"{skipped} service(s) skipped: a load is already running.", messages.WARNING)

    @admin.display(description="Catalog")
    def catalog_status(self, obj):
        info = harvest.state(obj)
        if info["state"] == "running":
            return "Loading…"
        if info["state"] == "failed":
            return f"Failed: {info['error']}"
        if info["state"] == "done":
            text = f"{obj.sources.count()} sources, loaded {date_format(obj.catalog_loaded_at, 'DATETIME_FORMAT')}"
            return f"{text}. {info['note']}" if info["note"] else text
        return "Not loaded yet"


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


# ---------------------------------------------------------------------------
# Category items: two-panel picker (ticket 03b)
# ---------------------------------------------------------------------------


def _match_key(service_id, dataset_id: str, source_id: str) -> str:
    return f"{service_id}|{dataset_id}|{source_id}"


def _item_entry(item: CategoryItem) -> dict:
    dataset_id, source_id = harvest.item_source_key(item)
    return {
        "key": f"item:{item.pk}",
        "match": _match_key(item.service_id, dataset_id, source_id),
        "title": item.title,
        "datasetTitle": item.dataset_title,
        "serviceTitle": item.service.title,
        "settingsUrl": reverse("admin:external_catalog_categoryitem_change", args=[item.pk]),
    }


def _source_entry(source: ServiceSource) -> dict:
    return {
        "key": f"src:{source.pk}",
        "match": _match_key(source.service_id, source.dataset_id, source.source_id),
        "title": source.title,
        "datasetTitle": source.dataset_title,
        "serviceTitle": source.service.title,
        "settingsUrl": None,
    }


class CategoryItemsWidget(forms.Widget):
    """Two lists -- available sources / items of this category -- driven by
    ``category-picker.js``. Submits one hidden JSON list of keys in display
    order: ``item:<uuid>`` keeps an existing item, ``src:<id>`` adds an
    indexed source."""

    template_name = "external_catalog/admin/category_items_widget.html"

    class Media:
        js = ("external_catalog/admin/category-picker.js",)
        css = {"all": ("external_catalog/admin/category-picker.css",)}

    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.services: list[dict] = []
        self.items: dict[str, CategoryItem] = {}
        self.allowed_service_ids: set = set()

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        keys = _parse_keys(value) if value not in (None, "") else list(self.items)
        context["widget"]["value"] = json.dumps(keys)
        context["picker_config"] = {
            "services": self.services,
            "entries": self._entries(keys),
            "confirmAbove": 50,
        }
        return context

    def _entries(self, keys) -> list[dict]:
        source_ids = [int(key[4:]) for key in keys if key.startswith("src:") and key[4:].isdigit()]
        sources = {
            f"src:{source.pk}": source
            for source in ServiceSource.objects.filter(
                pk__in=source_ids, service_id__in=self.allowed_service_ids
            ).select_related("service")
        }
        entries = []
        for key in keys:
            if key in self.items:
                entries.append(_item_entry(self.items[key]))
            elif key in sources:
                entries.append(_source_entry(sources[key]))
        return entries


def _parse_keys(value) -> list[str]:
    try:
        keys = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return []
    return [key for key in keys if isinstance(key, str)] if isinstance(keys, list) else []


class CategoryForm(forms.ModelForm):
    item_sources = forms.CharField(
        label="Items",
        required=False,
        widget=CategoryItemsWidget,
        help_text=(
            "Select on the left and press » to add (»» adds everything the filters show, e.g. "
            "a whole theme); select on the right and press « to remove. Titles and descriptions "
            "come from the service. Saving the category applies the changes."
        ),
    )

    class Meta:
        model = Category
        fields = "__all__"

    # Set by CategoryAdmin.get_form.
    request = None
    may_add_items = True
    may_remove_items = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.services = list(_services_for(self.request)) if self.request else []
        self.existing_items = {}
        if self.instance.pk:
            self.existing_items = {
                f"item:{item.pk}": item
                for item in self.instance.items.select_related("service").order_by("display_order", "title", "id")
            }
        widget = self.fields["item_sources"].widget
        widget.items = self.existing_items
        widget.allowed_service_ids = {service.pk for service in self.services}
        widget.services = [
            {
                "id": str(service.pk),
                "title": service.title,
                "type": service.service_type,
                "sourcesUrl": reverse("admin:external_catalog_externalservice_sources", args=[service.pk]),
                "catalogUrl": reverse("admin:external_catalog_externalservice_catalog", args=[service.pk]),
            }
            for service in self.services
        ]

    def clean_item_sources(self):
        """Resolve the submitted keys to kept items and sources to add."""
        raw = self.cleaned_data.get("item_sources")
        if raw in (None, ""):
            return {"order": [("item", item) for item in self.existing_items.values()], "removed": []}
        try:
            keys = json.loads(raw)
        except ValueError as exc:
            raise forms.ValidationError("The item list could not be read; reload the page.") from exc
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise forms.ValidationError("The item list could not be read; reload the page.")

        allowed = {service.pk for service in self.services}
        source_ids = [int(key[4:]) for key in keys if key.startswith("src:") and key[4:].isdigit()]
        sources = {
            f"src:{source.pk}": source
            for source in ServiceSource.objects.filter(
                pk__in=source_ids, service_id__in=allowed, geojson=True
            ).select_related("service")
        }
        order, seen = [], set()
        for key in keys:
            if key in self.existing_items:
                item = self.existing_items[key]
                entry, match = ("item", item), _match_key(item.service_id, *harvest.item_source_key(item))
            elif key in sources:
                source = sources[key]
                entry, match = ("src", source), _match_key(source.service_id, source.dataset_id, source.source_id)
            else:
                raise forms.ValidationError("Some selected sources are no longer available; reload the page.")
            if match in seen:
                raise forms.ValidationError(f"'{entry[1].title}' is in the list twice.")
            seen.add(match)
            order.append(entry)

        kept = {item.pk for kind, item in order if kind == "item"}
        removed = [item for item in self.existing_items.values() if item.pk not in kept]
        if removed and not self.may_remove_items:
            raise forms.ValidationError("You may not remove items from categories.")
        if any(kind == "src" for kind, _ in order) and not self.may_add_items:
            raise forms.ValidationError("You may not add items to categories.")
        return {"order": order, "removed": removed}


@admin.register(Category)
class CategoryAdmin(OrganizationOwnedAdminMixin, admin.ModelAdmin):
    form = CategoryForm
    list_display = ("title", "organization", "display_order", "visibility", "is_active", "item_count")
    list_filter = ("visibility", "is_active", "organization")
    search_fields = ("title", "slug", "description")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("id", "created_by", "created_at", "updated_at")
    ordering = ("display_order", "title")

    fieldsets = (
        (None, {"fields": ("id", "organization", "title", "slug", "description", "display_order")}),
        ("Items", {"fields": ("item_sources",), "classes": ("external-category-items",)}),
        (
            "Visibility",
            {"fields": ("visibility", "is_active"), "description": VISIBILITY_NOTE},
        ),
        ("Ownership", {"fields": ("created_by",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change=change, **kwargs)
        form_class.request = request
        form_class.may_add_items = request.user.has_perm("external_catalog.add_categoryitem")
        form_class.may_remove_items = request.user.has_perm("external_catalog.delete_categoryitem")
        return form_class

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_item_count=Count("items"))

    @admin.display(description="Items", ordering="_item_count")
    def item_count(self, obj):
        return obj._item_count

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        result = form.cleaned_data.get("item_sources")
        if result:
            apply_items(form.instance, result["order"], result["removed"], request.user)

    # ------------------------------------------------------------------
    # New category from a theme
    # ------------------------------------------------------------------
    def get_urls(self):
        urls = [
            path(
                "from-theme/",
                self.admin_site.admin_view(self._from_theme_view),
                name="external_catalog_category_from_theme",
            )
        ]
        return urls + super().get_urls()

    def _from_theme_view(self, request):
        if not (self.has_add_permission(request) and request.user.has_perm("external_catalog.add_categoryitem")):
            raise PermissionDenied
        services = [
            service
            for service in _services_for(request).filter(
                service_type=ExternalService.ServiceType.OGC_API_FEATURES
            )
            if service.catalog_loaded_at
        ]
        choices = _theme_choices(services)
        if request.method == "POST":
            choice = choices.get(request.POST.get("choice", ""))
            if choice is None:
                self.message_user(request, "Choose a service and theme.", messages.ERROR)
            else:
                category = create_category_from_theme(request, *choice)
                self.message_user(
                    request,
                    f"Created '{category.title}' with {category.items.count()} items. It is private and "
                    "inactive: review the items, then activate it.",
                )
                return HttpResponseRedirect(reverse("admin:external_catalog_category_change", args=[category.pk]))
        context = {
            **self.admin_site.each_context(request),
            "title": "New category from theme",
            "opts": self.opts,
            "choices": [
                {"value": value, "label": f"{service.title} — {label} ({count} collections)"}
                for value, (service, code, label, count) in choices.items()
            ],
            "services_without_catalog": [
                service.title
                for service in _services_for(request).filter(
                    service_type=ExternalService.ServiceType.OGC_API_FEATURES, catalog_loaded_at__isnull=True
                )
            ],
        }
        return TemplateResponse(request, "admin/external_catalog/category/from_theme.html", context)


def _theme_choices(services) -> dict:
    choices = {}
    for service in services:
        counts: dict[str, int] = {}
        for themes in ServiceSource.objects.filter(service=service, geojson=True).values_list("themes", flat=True):
            for code in themes:
                counts[code] = counts.get(code, 0) + 1
        for code, label in sorted(remote.THEMES.items(), key=lambda entry: entry[1]):
            if code in counts:
                choices[f"{service.pk}:{code}"] = (service, code, label, counts[code])
    return choices


@transaction.atomic
def create_category_from_theme(request, service, code, label, _count) -> Category:
    """A private, inactive category holding every map-able collection of a theme."""
    base = slugify(label)[:90] or "theme"
    slug, suffix = base, 2
    while Category.objects.filter(slug=slug).exists():
        slug, suffix = f"{base}-{suffix}", suffix + 1
    category = Category.objects.create(
        organization=service.organization,
        slug=slug,
        title=label,
        description=f"All {label.lower()} data from {service.title}.",
        visibility=Visibility.PRIVATE,
        is_active=False,
        created_by=request.user,
    )
    sources = ServiceSource.objects.filter(service=service, geojson=True, themes__contains=[code]).select_related(
        "service"
    )
    apply_items(category, [("src", source) for source in sources.order_by("dataset_title", "title", "id")], [], request.user)
    return category


@transaction.atomic
def apply_items(category: Category, order, removed, user) -> None:
    """Delete removed items, create new ones from sources, store the order."""
    for item in removed:
        item.delete()
    ordered = []
    for position, (kind, value) in enumerate(order):
        if kind == "item":
            value.display_order = position
            ordered.append(value)
        else:
            CategoryItem(
                category=category,
                service=value.service,
                display_order=position,
                created_by=user,
                **harvest.source_fields(value),
                **harvest.item_values(value),
            ).save()
    CategoryItem.objects.bulk_update(ordered, ["display_order"])


# ---------------------------------------------------------------------------
# Items: settings only (source, title and description come from the service)
# ---------------------------------------------------------------------------


class CategoryItemForm(forms.ModelForm):
    class Meta:
        model = CategoryItem
        fields = ("color", "min_zoom", "default_properties", "default_filter")
        widgets = {
            "default_properties": forms.Textarea(attrs={"rows": 2}),
            "default_filter": forms.Textarea(attrs={"rows": 3}),
            "color": forms.TextInput(attrs={"type": "color"}),
        }

    def _list_value(self, field):
        # An empty JSON textarea submits None; these fields are lists.
        value = self.cleaned_data.get(field)
        return [] if value is None else value

    def clean_default_properties(self):
        return self._list_value("default_properties")

    def clean_default_filter(self):
        return self._list_value("default_filter")


@admin.register(CategoryItem)
class CategoryItemAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    """Item list across categories and per-item map settings.

    Items are added, removed and ordered in their category's picker; here
    only the presentation/loading settings can be changed.
    """

    org_lookup = "category__organization__slug"
    form = CategoryItemForm
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
    search_fields = ("title", "dataset_title", "ogc_collection_id", "sta_service_name", "sta_layer_name")
    list_select_related = ("category", "service")
    ordering = ("category__display_order", "category__title", "display_order", "title")
    readonly_fields = (
        "category",
        "service",
        "source",
        "title",
        "dataset_title",
        "description",
        "availability_state",
        "feature_count",
        "last_checked_at",
        "last_check_error",
    )
    fieldsets = (
        (
            "Source (from the service)",
            {"fields": ("category", "service", "source", "title", "dataset_title", "description")},
        ),
        ("Map", {"fields": ("color", "min_zoom")}),
        (
            "Default query (OGC API only)",
            {
                "fields": ("default_properties", "default_filter"),
                "description": (
                    'Attributes as a JSON list, e.g. ["name", "status"]. Filter, e.g. '
                    '[{"property": "breite", "operator": "gte", "value": 5}].'
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

    @admin.display(description="Source")
    def source(self, obj):
        if obj.service.service_type == ExternalService.ServiceType.SENSORTHINGS:
            return f"{obj.sta_service_name} / {obj.sta_layer_name}"
        if obj.ogc_dataset_id:
            return f"{obj.ogc_dataset_id} / {obj.ogc_collection_id}"
        return obj.ogc_collection_id

    def has_add_permission(self, request):
        return False
