import json

from django.contrib import admin

from .models import GeoContext


@admin.register(GeoContext)
class GeoContextAdmin(admin.ModelAdmin):
    """Admin interface for GeoContext model."""

    list_display = ("id", "content_preview", "created_by", "created_at")
    list_filter = ("created_at",)
    search_fields = ("id",)
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)

    fieldsets = (
        (None, {"fields": ("id", "content")}),
        ("Ownership", {"fields": ("created_by",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Content Preview")
    def content_preview(self, obj: GeoContext) -> str:
        """Return a truncated preview of the JSON content."""
        blocks = (obj.content or {}).get("blocks") or []
        if not blocks:
            return "(empty)"
        as_text = json.dumps(blocks, ensure_ascii=False)
        return as_text[:75] + "..." if len(as_text) > 75 else as_text
