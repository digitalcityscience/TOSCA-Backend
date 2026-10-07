from django import forms
from django.contrib import admin
from django.contrib.admin.options import IS_POPUP_VAR
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html

from tosca_api.apps.organizations.permissions import OrgScopedAdminMixin
from tosca_api.apps.geocontext.widgets import EditorJsWidget
from tosca_api.apps.core.editorjs import render_content_media_urls

from .forms import GeoStorySceneLayerForm
from .models import GeoStory, GeoStoryScene, GeoStorySceneLayer


class GeoStoryAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial["content"] = render_content_media_urls(self.instance.content)

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
    fields = ("order", "title", "layer_count", "camera")
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


@admin.register(GeoStory)
class GeoStoryAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    org_lookup = "campaign__organization__slug"

    list_display = ("title", "hero_image_thumbnail", "status", "campaign", "author", "created_at")
    list_filter = ("status", "created_at", "campaign")
    search_fields = ("title", "summary", "about_author")
    form = GeoStoryAdminForm
    autocomplete_fields = ["campaign", "author"]
    inlines = [GeoStorySceneInline]
    readonly_fields = ("hero_image_preview", "scene_tools")
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
    )

    @admin.display(description="Scenes")
    def scene_tools(self, obj: GeoStory) -> str:
        if obj is None or obj.pk is None:
            return "Save the story first to add map scenes."
        url = reverse("admin:geostories_geostoryscene_add")
        return format_html(
            '<a class="addlink" href="{}?geostory={}">Add scene</a>', url, obj.pk
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


class GeoStorySceneLayerInline(admin.TabularInline):
    model = GeoStorySceneLayer
    form = GeoStorySceneLayerForm
    fields = (
        "layer",
        "style_assignment",
        "render_layer_ids",
        "display_order",
        "opacity",
        "feature_mode",
        "feature_id_attribute",
        "feature_ids",
    )
    extra = 1
    autocomplete_fields = ("layer",)

    class Media:
        js = ("geostories/js/admin_geostory_scene.js",)


@admin.register(GeoStoryScene)
class GeoStorySceneAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    org_lookup = "geostory__campaign__organization__slug"

    list_display = ("title", "geostory", "order", "layer_count")
    search_fields = ("title", "geostory__title")
    autocomplete_fields = ("geostory",)
    inlines = [GeoStorySceneLayerInline]
    readonly_fields = ("story_link",)
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
                    "Leave the center empty to fit the map to the scene's layers."
                ),
            },
        ),
        ("Transition", {"fields": ("transition", "duration_ms")}),
    )

    def get_model_perms(self, request):
        # Scenes are reached through their story, not the admin index.
        return {}

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
