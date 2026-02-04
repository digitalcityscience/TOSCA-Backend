from django.contrib import admin

from .models import GeoContext


@admin.register(GeoContext)
class GeoContextAdmin(admin.ModelAdmin):
    """Admin interface for GeoContext model."""

    list_display = ("id", "content_type", "content_preview", "created_by", "created_at")
    list_filter = ("content_type", "created_at")
    search_fields = ("content",)
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)

    fieldsets = (
        (None, {"fields": ("id", "content", "content_type")}),
        ("Ownership", {"fields": ("created_by",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Content Preview")
    def content_preview(self, obj: GeoContext) -> str:
        """Return a truncated preview of the content."""
        if obj.content:
            return obj.content[:75] + "..." if len(obj.content) > 75 else obj.content
        return "(empty)"
