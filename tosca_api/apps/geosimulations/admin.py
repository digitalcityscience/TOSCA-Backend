"""Admin for GeoSimulations (read-mostly).

Runs, outputs, and assets enter through package imports
(``import_simulation_package``, Task 12.3), so their admin only lets staff
edit what is authored in TOSCA: titles, descriptions, and reader status.
Every admin is row-scoped to the caller's organization.
"""

import json

from django import forms
from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from tosca_api.apps.external_catalog.admin import OrganizationOwnedAdminMixin
from tosca_api.apps.geocontext.widgets import EditorJsWidget
from tosca_api.apps.organizations.permissions import OrgScopedAdminMixin

from .models import SimulationAsset, SimulationModel, SimulationOutput, SimulationRun


def _pretty_json(value) -> str:
    if value in (None, {}, []):
        return "—"
    return format_html(
        '<pre style="white-space: pre-wrap; max-height: 24rem; overflow: auto">{}</pre>',
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True),
    )


class SimulationModelAdminForm(forms.ModelForm):
    class Meta:
        model = SimulationModel
        fields = "__all__"
        widgets = {"description_content": EditorJsWidget(profile="description")}


class SimulationRunAdminForm(forms.ModelForm):
    class Meta:
        model = SimulationRun
        fields = "__all__"
        widgets = {"description_content": EditorJsWidget(profile="description")}


class SimulationRunInline(admin.TabularInline):
    """Read-only run overview on the model page; runs are edited on their own page."""

    model = SimulationRun
    fields = ("title", "status", "execution_state", "time_start", "model_version")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SimulationModel)
class SimulationModelAdmin(OrganizationOwnedAdminMixin, admin.ModelAdmin):
    form = SimulationModelAdminForm
    list_display = ("title", "key", "organization", "status", "run_count", "updated_at")
    list_filter = ("status", "organization")
    search_fields = ("title", "key", "summary")
    raw_id_fields = ("preview_image",)
    inlines = [SimulationRunInline]
    readonly_fields = (
        "id",
        "parameter_schema_display",
        "default_parameters_display",
        "created_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("id", "organization", "key", "title", "summary", "status")}),
        ("Description", {"fields": ("description_content", "preview_image")}),
        (
            "Parameters",
            {
                "fields": ("parameter_schema_display", "default_parameters_display"),
                "description": "Set by imports; used by user-triggered runs (Phase 4).",
            },
        ),
        ("Record", {"fields": ("created_by", "created_at", "updated_at")}),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            # Imports match models by key; renaming one would orphan the next import.
            readonly.append("key")
        return readonly

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_run_count=Count("runs"))

    @admin.display(description="Runs", ordering="_run_count")
    def run_count(self, obj: SimulationModel) -> int:
        return obj._run_count

    @admin.display(description="Parameter schema")
    def parameter_schema_display(self, obj: SimulationModel):
        return _pretty_json(obj.parameter_schema)

    @admin.display(description="Default parameters")
    def default_parameters_display(self, obj: SimulationModel):
        return _pretty_json(obj.default_parameters)


class SimulationOutputInline(admin.TabularInline):
    """Outputs come from the package; only their order and titles are authored here."""

    model = SimulationOutput
    fields = ("display_order", "title", "role", "kind", "layer", "asset", "summary")
    readonly_fields = ("role", "kind", "layer", "asset", "summary")
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Details")
    def summary(self, obj: SimulationOutput) -> str:
        metadata = obj.metadata or {}
        if obj.kind == SimulationOutput.Kind.VECTOR_LAYER:
            return f"{metadata.get('features', '?')} features"
        if obj.kind == SimulationOutput.Kind.TRIPS:
            size = obj.asset.size / 1e6 if obj.asset_id else 0
            return f"{metadata.get('agents', '?')} agents · {size:.1f} MB"
        if obj.kind == SimulationOutput.Kind.TABLE:
            rows = obj.data.get("rows", []) if isinstance(obj.data, dict) else obj.data or []
            return f"{len(rows)} rows"
        return "—"


@admin.register(SimulationRun)
class SimulationRunAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    org_lookup = "model__organization__slug"

    form = SimulationRunAdminForm
    list_display = ("title", "model", "status", "execution_state", "time_start", "created_at")
    list_filter = ("status", "execution_state", "model")
    search_fields = ("title", "key", "model__title")
    list_select_related = ("model",)
    inlines = [SimulationOutputInline]
    readonly_fields = (
        "id",
        "model",
        "key",
        "model_version",
        "seed",
        "params_hash",
        "execution_state",
        "execution_error",
        "time_start",
        "time_end",
        "timezone",
        "bounds",
        "parameters_display",
        "kpis_display",
        "manifest_display",
        "created_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("id", "model", "key", "title", "status")}),
        ("Description", {"fields": ("description_content",)}),
        (
            "Execution",
            {
                "fields": (
                    "execution_state",
                    "execution_error",
                    "model_version",
                    "seed",
                    "params_hash",
                    "parameters_display",
                )
            },
        ),
        ("Simulated time and area", {"fields": ("time_start", "time_end", "timezone", "bounds")}),
        ("Results", {"fields": ("kpis_display",)}),
        (
            "Provenance",
            {"fields": ("manifest_display", "created_by", "created_at", "updated_at"),
             "classes": ("collapse",)},
        ),
    )

    def has_add_permission(self, request):
        # Runs are created by package imports (Task 12.3), never by hand.
        return False

    @admin.display(description="Parameters")
    def parameters_display(self, obj: SimulationRun):
        return _pretty_json(obj.parameters)

    @admin.display(description="Key figures")
    def kpis_display(self, obj: SimulationRun):
        return _pretty_json(obj.kpis)

    @admin.display(description="Package manifest")
    def manifest_display(self, obj: SimulationRun):
        return _pretty_json(obj.manifest)


@admin.register(SimulationAsset)
class SimulationAssetAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    org_lookup = "owner_org__slug"

    list_display = ("__str__", "mime", "size", "storage_alias", "owner_org", "created_at")
    list_filter = ("storage_alias", "mime", "owner_org")
    search_fields = ("storage_path", "original_name", "sha256")
    readonly_fields = (
        "id",
        "storage_path",
        "original_name",
        "mime",
        "size",
        "sha256",
        "storage_alias",
        "owner_org",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Assets are immutable files; the storage lifecycle moves them, not staff.
        return False
