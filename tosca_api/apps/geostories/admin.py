from django.contrib import admin

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
    list_display = ("title", "status", "campaign", "author", "created_at")
    list_filter = ("status", "created_at", "campaign")
    search_fields = ("title", "summary")
    autocomplete_fields = ["campaign", "author", "context"]
    inlines = [GeoStoryLayerInline]
