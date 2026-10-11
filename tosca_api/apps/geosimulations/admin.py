"""Admin for GeoSimulations (read-mostly).

Runs, outputs, and assets enter through package imports
(``import_simulation_package``, Task 12.3), so their admin only lets staff
edit what is authored in TOSCA: titles, descriptions, and reader status.
Every admin is row-scoped to the caller's organization.
"""

import json
import tempfile
from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from tosca_api.apps.external_catalog.admin import OrganizationOwnedAdminMixin
from tosca_api.apps.geocontext.widgets import EditorJsWidget
from tosca_api.apps.organizations.models import Organization
from tosca_api.apps.organizations.permissions import (
    OrgScopedAdminMixin,
    get_request_org_context,
    resolve_write_organization,
)

from .importer import PackageImportError, import_package
from .models import SimulationAsset, SimulationModel, SimulationOutput, SimulationRun
from .packages import PackageValidationError


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


class PackageImportForm(forms.Form):
    package = forms.FileField(
        help_text="A simulation package (.zip), for example written by geodata-science "
        "bike-export-tosca."
    )
    organization = forms.ModelChoiceField(
        queryset=Organization.objects.order_by("name"),
        required=False,
        help_text="Owning organization (platform administrators only).",
    )
    publish = forms.BooleanField(
        required=False, help_text="Publish the run, and its simulation model if it is a draft."
    )
    replace = forms.BooleanField(
        required=False, help_text="Replace an existing run with the same key, including its files."
    )


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
    fields = ("display_order", "title", "role", "kind", "asset", "summary")
    readonly_fields = ("role", "kind", "asset", "summary")
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Details")
    def summary(self, obj: SimulationOutput) -> str:
        metadata = obj.metadata or {}
        size = f"{obj.asset.size / 1e6:.1f} MB" if obj.asset_id else ""
        if obj.kind == SimulationOutput.Kind.VECTOR_LAYER:
            return (
                f"{metadata.get('features', '?')} {metadata.get('geometry_type', '')} features"
                f" · z{metadata.get('minzoom', '?')}–{metadata.get('maxzoom', '?')} · {size}"
            )
        if obj.kind == SimulationOutput.Kind.TRIPS:
            return f"{metadata.get('agents', '?')} agents · {size}"
        if obj.kind == SimulationOutput.Kind.TABLE:
            rows = obj.data.get("rows", []) if isinstance(obj.data, dict) else obj.data or []
            return f"{len(rows)} rows"
        return size or "—"


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
        # Runs are created by package imports, never by hand (see import_view).
        return False

    def get_urls(self):
        urls = [
            path(
                "import/",
                self.admin_site.admin_view(self.import_view),
                name="geosimulations_simulationrun_import",
            )
        ]
        return urls + super().get_urls()

    def _can_import(self, request) -> bool:
        return self._is_active_staff(request) and request.user.has_perm(
            "geosimulations.add_simulationrun"
        )

    def changelist_view(self, request, extra_context=None):
        extra_context = {**(extra_context or {}), "can_import_packages": self._can_import(request)}
        return super().changelist_view(request, extra_context=extra_context)

    def import_view(self, request):
        """Upload a package and import it with the same service as the command."""
        if not self._can_import(request):
            raise PermissionDenied
        _roles, _org_slug, exempt = get_request_org_context(request)
        choose_organization = request.user.is_superuser or exempt
        form = PackageImportForm(request.POST or None, request.FILES or None)
        if not choose_organization:
            del form.fields["organization"]

        if request.method == "POST" and form.is_valid():
            organization = (
                form.cleaned_data.get("organization")
                if choose_organization
                else resolve_write_organization(request)
            )
            if organization is None:
                form.add_error(
                    "organization" if choose_organization else None,
                    "Choose the organization that owns this simulation.",
                )
            elif form.cleaned_data["replace"] and not request.user.has_perm(
                "geosimulations.delete_simulationrun"
            ):
                form.add_error("replace", "Replacing a run needs permission to delete runs.")
            else:
                run = self._import_upload(request, form, organization)
                if run is not None:
                    return HttpResponseRedirect(
                        reverse("admin:geosimulations_simulationrun_change", args=[run.pk])
                    )

        context = {
            **self.admin_site.each_context(request),
            "title": "Import simulation package",
            "opts": self.opts,
            "form": form,
        }
        return TemplateResponse(
            request, "admin/geosimulations/simulationrun/import_package.html", context
        )

    def _import_upload(self, request, form, organization):
        upload = form.cleaned_data["package"]
        with tempfile.TemporaryDirectory(prefix="geosim-upload-") as staging:
            archive = Path(staging) / "package.zip"
            with archive.open("wb") as handle:
                for chunk in upload.chunks():
                    handle.write(chunk)
            try:
                result = import_package(
                    archive,
                    organization=organization,
                    user=request.user,
                    publish=form.cleaned_data["publish"],
                    replace=form.cleaned_data["replace"],
                )
            except PackageValidationError as exc:
                for issue in exc.issues:
                    form.add_error("package", str(issue))
                return None
            except PackageImportError as exc:
                form.add_error(None, str(exc))
                return None
        for warning in result.warnings:
            self.message_user(request, f"Warning: {warning}", messages.WARNING)
        self.message_user(
            request,
            f"Imported '{result.run.title}' with {result.run.outputs.count()} outputs "
            f"({result.run.get_status_display().lower()}).",
        )
        return result.run

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
