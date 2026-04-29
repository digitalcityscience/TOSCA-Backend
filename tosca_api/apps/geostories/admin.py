from django.contrib import admin
from django.utils.html import format_html

from .forms import GeoStoryLayerFormSet
from .models import GeoStory, GeoStoryLayer


class GeoStoryLayerInline(admin.TabularInline):
    model = GeoStoryLayer
    formset = GeoStoryLayerFormSet
    extra = 1
    autocomplete_fields = ["layer"]

    class Media:
        js = ("geostories/js/admin_geostory.js",)


@admin.register(GeoStory)
class GeoStoryAdmin(admin.ModelAdmin):
    list_display = ("title", "hero_image_thumbnail", "status", "campaign", "author", "created_at")
    list_filter = ("status", "created_at", "campaign")
    search_fields = ("title", "summary")
    autocomplete_fields = ["campaign", "author", "context"]
    inlines = [GeoStoryLayerInline]
    readonly_fields = ("hero_image_preview",)
    fieldsets = (
        (None, {"fields": ("title", "summary", "status", "campaign", "author", "context")}),
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
