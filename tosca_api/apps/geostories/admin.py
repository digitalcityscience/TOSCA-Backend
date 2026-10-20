import json
import uuid

from django import forms
from django.contrib import admin
from django.contrib.admin.options import IS_POPUP_VAR
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from tosca_api.apps.organizations.permissions import OrgScopedAdminMixin
from tosca_api.apps.geocontext.widgets import EditorJsWidget
from tosca_api.apps.core.editorjs import render_content_media_urls
from tosca_api.apps.geosimulations.models import SimulationOutput, Status

from .forms import (
    GeoStoryResultPresentationForm,
    GeoStorySceneLayerForm,
    GeoStorySceneSimulationForm,
    SimulationOutputChoiceField,
)
from .models import (
    GeoStory,
    GeoStoryResultPresentation,
    GeoStoryScene,
    GeoStorySceneLayer,
    GeoStorySceneSimulation,
)
from .scene_editor import basemap_style, preview_view


class GeoStoryAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial["content"] = render_content_media_urls(self.instance.content)
        # The content editor's "Map scene" tool offers the story's saved scenes.
        scenes = (
            [
                {
                    "id": str(scene.pk),
                    "title": scene.title,
                    "order": scene.order,
                    "url": reverse("admin:geostories_geostoryscene_change", args=[scene.pk]),
                }
                for scene in self.instance.scenes.all()
            ]
            if self.instance.pk
            else []
        )
        self.fields["content"].widget.attrs["data-editorjs-map-scenes"] = json.dumps(scenes)
        results = (
            [
                {
                    "id": str(result.pk),
                    "caption": str(result),
                    "view": result.get_view_type_display(),
                    "url": reverse(
                        "admin:geostories_geostoryresultpresentation_change",
                        args=[result.pk],
                    ),
                }
                for result in self.instance.result_presentations.select_related("output")
            ]
            if self.instance.pk
            else []
        )
        self.fields["content"].widget.attrs["data-editorjs-simulation-results"] = json.dumps(
            results
        )

    class Meta:
        model = GeoStory
        fields = "__all__"
        widgets = {"content": EditorJsWidget()}


def _camera_summary(scene: GeoStoryScene) -> str:
    if scene.center_lng is None or scene.center_lat is None:
        return "Fit to layers"
    return (
        f"{scene.center_lat:.4f}, {scene.center_lng:.4f} · z{scene.zoom:.1f}"
        f" · bearing {scene.bearing:.0f}° · pitch {scene.pitch:.0f}°"
    )


class GeoStorySceneInline(admin.TabularInline):
    """Read-only scene overview; scenes are edited on their own page."""

    model = GeoStoryScene
    fields = ("order", "title", "layer_count", "camera", "anchor")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Layers")
    def layer_count(self, obj: GeoStoryScene) -> int:
        return obj.scene_layers.count()

    @admin.display(description="Camera")
    def camera(self, obj: GeoStoryScene) -> str:
        return _camera_summary(obj)

    @admin.display(description="Anchor in story")
    def anchor(self, obj: GeoStoryScene) -> str:
        if str(obj.pk) in obj.geostory.anchored_scene_ids():
            return "Anchored"
        if obj in obj.geostory.unreachable_scenes():
            return format_html('<strong style="color: var(--error-fg)">Not anchored — never shown</strong>')
        return "Shown before the first anchor"


class GeoStoryResultPresentationInline(admin.TabularInline):
    """Read-only overview; result views are edited on their own page."""

    model = GeoStoryResultPresentation
    fields = ("caption", "view_type", "output", "anchor")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Anchor in story")
    def anchor(self, obj: GeoStoryResultPresentation) -> str:
        return (
            "Anchored"
            if str(obj.pk) in obj.story.anchored_simulation_result_ids()
            else format_html(
                '<strong style="color: var(--error-fg)">{}</strong>',
                "Not anchored — never shown",
            )
        )


@admin.register(GeoStory)
class GeoStoryAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    org_lookup = "campaign__organization__slug"

    list_display = ("title", "hero_image_thumbnail", "status", "campaign", "author", "created_at")
    list_filter = ("status", "created_at", "campaign")
    search_fields = ("title", "summary", "about_author")
    form = GeoStoryAdminForm
    autocomplete_fields = ["campaign", "author"]
    inlines = []
    readonly_fields = ("hero_image_preview", "scene_tools", "result_tools")

    class Media:
        css = {"all": ("geostories/css/editorjs_map_scene.css",)}
        js = (
            "geostories/js/editorjs_map_scene.js",
            "geostories/js/editorjs_simulation_result.js",
        )
    fieldsets = (
        (None, {"fields": ("title", "summary", "status", "campaign", "author")}),
        ("About the author", {"fields": ("about_author",)}),
        ("Story", {"fields": ("content",)}),
        (
            "Hero image",
            {
                "fields": ("hero_image", "hero_image_alt", "hero_image_preview"),
                "description": (
                    "API responses expose hero_image_url as an absolute URL via "
                    "request.build_absolute_uri; the model field stores the "
                    "relative storage path."
                ),
            },
        ),
        (
            "Map scenes",
            {
                "fields": ("scene_tools",),
                "description": (
                    "Each scene sets the map's layers, styles and camera. "
                    "Readers switch scenes while scrolling through the story."
                ),
            },
        ),
        (
            "Simulation results",
            {
                "fields": ("result_tools",),
                "description": (
                    "Create a validated view of a published structured output, then insert "
                    "it anywhere in the story with the “Simulation result” editor block."
                ),
            },
        ),
    )

    @admin.display(description="Scenes")
    def scene_tools(self, obj: GeoStory) -> str:
        if obj is None or obj._state.adding:
            return "Save the story first to add map scenes."
        url = reverse("admin:geostories_geostoryscene_add")
        anchored = obj.anchored_scene_ids()
        scenes = []
        for index, scene in enumerate(obj.scenes.all()):
            is_anchored = str(scene.pk) in anchored
            scenes.append(
                {
                    "object": scene,
                    "url": reverse("admin:geostories_geostoryscene_change", args=[scene.pk]),
                    "layers": scene.scene_layers.count(),
                    "camera": _camera_summary(scene),
                    "anchor": (
                        "Anchored"
                        if is_anchored
                        else "Shown before the first anchor"
                        if index == 0
                        else "Not anchored — never shown"
                    ),
                    "missing": index > 0 and not is_anchored,
                }
            )
        return mark_safe(
            render_to_string(
                "admin/geostories/geostory/scene_collection.html",
                {"add_url": f"{url}?geostory={obj.pk}", "scenes": scenes},
            )
        )

    @admin.display(description="Result views")
    def result_tools(self, obj: GeoStory) -> str:
        if obj is None or obj._state.adding:
            return "Save the story first to add simulation results."
        url = reverse("admin:geostories_geostoryresultpresentation_add")
        anchored = obj.anchored_simulation_result_ids()
        results = [
            {
                "object": result,
                "url": reverse(
                    "admin:geostories_geostoryresultpresentation_change",
                    args=[result.pk],
                ),
                "source": "Run" if result.output.run_id else "Comparison",
                "anchor": "Anchored" if str(result.pk) in anchored else "Not anchored — never shown",
                "missing": str(result.pk) not in anchored,
            }
            for result in obj.result_presentations.select_related(
                "output__run", "output__comparison"
            )
        ]
        return mark_safe(
            render_to_string(
                "admin/geostories/geostory/result_collection.html",
                {"add_url": f"{url}?story={obj.pk}", "results": results},
            )
        )

    @admin.display(description="Hero")
    def hero_image_thumbnail(self, obj: GeoStory) -> str:
        if not obj.hero_image:
            return "—"
        return format_html(
            '<img src="{}" alt="{}" style="max-height:40px;max-width:80px;'
            'object-fit:cover;border-radius:2px;" />',
            obj.hero_image.url,
            obj.hero_image_alt or "",
        )

    @admin.display(description="Hero image preview")
    def hero_image_preview(self, obj: GeoStory) -> str:
        if not obj or not obj.hero_image:
            return "No image uploaded."
        return format_html(
            '<img src="{}" alt="{}" style="max-height:240px;max-width:480px;'
            'object-fit:contain;" />',
            obj.hero_image.url,
            obj.hero_image_alt or "",
        )


class GeoStorySceneLayerInline(admin.StackedInline):
    model = GeoStorySceneLayer
    form = GeoStorySceneLayerForm
    fields = (
        ("layer", "style_assignment"),
        ("display_order", "opacity"),
        "render_layer_ids",
        ("feature_mode", "feature_id_attribute"),
        "feature_ids",
    )
    extra = 0
    autocomplete_fields = ("layer",)

    class Media:
        js = ("geostories/js/admin_geostory_scene.js",)


@admin.register(GeoStoryResultPresentation)
class GeoStoryResultPresentationAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    """Configure a reusable, schema-checked presentation and anchor it in EditorJS."""

    org_lookup = "story__campaign__organization__slug"
    form = GeoStoryResultPresentationForm
    fields = (
        "story",
        "output",
        "caption",
        "view_type",
        "table_columns",
        "sort_field",
        "sort_direction",
        "metrics",
        "category_field",
        "x_field",
        "series_fields",
    )
    autocomplete_fields = ("story",)
    list_display = ("caption", "story", "view_type", "output")

    class Media:
        css = {"all": ("geostories/css/result_presentation.css",)}
        js = ("geostories/js/admin_result_presentation.js",)

    def get_model_perms(self, request):
        # Result views are reached through their story, not the admin index.
        return {}

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        story_id = request.GET.get("story")
        if story_id:
            initial["story"] = story_id
        return initial

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "output":
            kwargs["queryset"] = self._output_queryset()
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "output":
            field.widget.attrs.update(
                {
                    "data-result-schema-url": reverse(
                        "admin:geostories_geostoryresultpresentation_output_schema"
                    ),
                    "class": "result-output-select",
                }
            )
        return field

    @staticmethod
    def _output_queryset():
        return (
            SimulationOutput.objects.filter(kind=SimulationOutput.Kind.TABLE)
            .filter(
                Q(run__status=Status.PUBLISHED, run__model__status=Status.PUBLISHED)
                | Q(
                    comparison__status=Status.PUBLISHED,
                    comparison__model__status=Status.PUBLISHED,
                )
            )
            .select_related("run__model", "comparison__model")
            .order_by("run__title", "comparison__title", "display_order", "title")
        )

    def get_urls(self):
        return [
            path(
                "output-schema/",
                self.admin_site.admin_view(self.output_schema_view),
                name="geostories_geostoryresultpresentation_output_schema",
            )
        ] + super().get_urls()

    def output_schema_view(self, request):
        output = get_object_or_404(self._output_queryset(), pk=request.GET.get("output"))
        from tosca_api.apps.geosimulations.output_contracts import normalize_dataset

        dataset = normalize_dataset(output.data, metadata=output.metadata)
        metric_index = next(
            (
                index
                for index, field in enumerate(dataset["fields"])
                if field.get("semantic") == "metric" or field["key"] == "metric"
            ),
            None,
        )
        metrics = (
            [str(row[metric_index]) for row in dataset["rows"] if row[metric_index] is not None]
            if metric_index is not None
            else []
        )
        return JsonResponse(
            {
                "source": "Run" if output.run_id else "Comparison",
                "owner": output.owner.title,
                "output": output.title,
                "role": output.role,
                "profile": dataset["profile"],
                "fields": dataset["fields"],
                "metrics": metrics,
            }
        )

    def _story_redirect(self, request, obj, default):
        if IS_POPUP_VAR not in request.POST and "_continue" not in request.POST:
            return HttpResponseRedirect(
                reverse("admin:geostories_geostory_change", args=[obj.story_id])
            )
        return default

    def response_add(self, request, obj, post_url_continue=None):
        return self._story_redirect(
            request, obj, super().response_add(request, obj, post_url_continue)
        )

    def response_change(self, request, obj):
        return self._story_redirect(request, obj, super().response_change(request, obj))


class GeoStorySceneSimulationInline(admin.StackedInline):
    """At most one simulation run per scene: map layers plus a replay window."""

    model = GeoStorySceneSimulation
    form = GeoStorySceneSimulationForm
    fields = (
        "run",
        "map_outputs",
        "display_order",
        ("show_replay", "loop"),
        ("replay_start_s", "replay_end_s"),
        "playback_rate",
    )
    extra = 0
    max_num = 1
    verbose_name = "Simulation"
    verbose_name_plural = "Simulation (shown in the story, not in the preview above)"


@admin.register(GeoStoryScene)
class GeoStorySceneAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    org_lookup = "geostory__campaign__organization__slug"

    list_display = ("title", "geostory", "order", "layer_count")
    search_fields = ("title", "geostory__title")
    autocomplete_fields = ("geostory",)
    inlines = [GeoStorySceneLayerInline, GeoStorySceneSimulationInline]
    readonly_fields = ("story_link",)
    change_form_template = "admin/geostories/geostoryscene/change_form.html"
    fieldsets = (
        (None, {"fields": ("geostory", "story_link", "title", "caption", "order")}),
        (
            "Camera",
            {
                "fields": (
                    ("center_lng", "center_lat"),
                    "zoom",
                    ("bearing", "pitch"),
                    "bounds",
                ),
                "description": (
                    "Frame the map and press “Capture view”. Leave the center empty "
                    "to fit the map to the scene's layers."
                ),
            },
        ),
        ("Transition", {"fields": ("transition", "duration_ms")}),
    )

    def get_model_perms(self, request):
        # Scenes are reached through their story, not the admin index.
        return {}

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if initial.get("geostory"):
            try:
                initial["order"] = GeoStoryScene.next_order_for(initial["geostory"])
            except ValidationError:
                # A malformed ?geostory= would otherwise crash the autocomplete widget.
                del initial["geostory"]
        return initial

    def get_deleted_objects(self, objs, request):
        deleted, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        # A deleted scene would leave a dangling anchor that blocks the next
        # story save; make authors remove the anchor first.
        for scene in objs:
            if str(scene.pk) in scene.geostory.anchored_scene_ids():
                protected.append(
                    f"Map scene anchor for “{scene.title}” in the text of story "
                    f"“{scene.geostory.title}” — remove the anchor first"
                )
        return deleted, model_count, perms_needed, protected

    def get_urls(self):
        preview = path(
            "preview/",
            self.admin_site.admin_view(lambda request: preview_view(request, self)),
            name="geostories_geostoryscene_preview",
        )
        simulation_outputs = path(
            "simulation-outputs/",
            self.admin_site.admin_view(self.simulation_outputs_view),
            name="geostories_geostoryscene_simulation_outputs",
        )
        return [preview, simulation_outputs, *super().get_urls()]

    def simulation_outputs_view(self, request):
        from tosca_api.apps.geosimulations.models import SimulationOutput, Status

        outputs = SimulationOutput.objects.none()
        try:
            run_id = uuid.UUID(request.GET.get("run", ""))
        except (TypeError, ValueError):
            run_id = None
        if run_id:
            outputs = (
                SimulationOutput.objects.filter(
                    run_id=run_id,
                    run__status=Status.PUBLISHED,
                    run__model__status=Status.PUBLISHED,
                    kind=SimulationOutput.Kind.VECTOR_LAYER,
                )
                .select_related("run", "asset")
                .order_by("display_order", "created_at")
            )
        labeler = SimulationOutputChoiceField(queryset=SimulationOutput.objects.none())
        return JsonResponse(
            {
                "outputs": [
                    {"id": str(output.pk), "label": labeler.label_from_instance(output)}
                    for output in outputs
                ]
            }
        )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = {
            **(extra_context or {}),
            "scene_editor_config": {
                "previewUrl": reverse("admin:geostories_geostoryscene_preview"),
                "simulationOutputsUrl": reverse(
                    "admin:geostories_geostoryscene_simulation_outputs"
                ),
                "basemap": basemap_style(),
            },
        }
        return super().changeform_view(request, object_id, form_url, extra_context)

    @admin.display(description="Layers")
    def layer_count(self, obj: GeoStoryScene) -> int:
        return obj.scene_layers.count()

    @admin.display(description="Story")
    def story_link(self, obj: GeoStoryScene) -> str:
        if obj is None or obj.geostory_id is None:
            return "—"
        url = reverse("admin:geostories_geostory_change", args=[obj.geostory_id])
        return format_html('<a href="{}">Back to “{}”</a>', url, obj.geostory)

    def _story_redirect(self, request, obj, default):
        if {"_continue", "_addanother", IS_POPUP_VAR} & set(request.POST):
            return default
        url = reverse("admin:geostories_geostory_change", args=[obj.geostory_id])
        return HttpResponseRedirect(url)

    def response_add(self, request, obj, post_url_continue=None):
        return self._story_redirect(
            request, obj, super().response_add(request, obj, post_url_continue)
        )

    def response_change(self, request, obj):
        return self._story_redirect(request, obj, super().response_change(request, obj))
