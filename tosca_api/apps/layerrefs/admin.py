from django.contrib import admin

from .models import LayerRef


@admin.register(LayerRef)
class LayerRefAdmin(admin.ModelAdmin):
    list_display = ("layer_name", "created_at", "updated_at")
    search_fields = ("layer_name",)
    ordering = ("layer_name",)
