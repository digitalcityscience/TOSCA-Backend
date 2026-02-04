from django.contrib import admin

from .models import Campaign


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    """Admin interface for Campaign model."""

    list_display = ("title", "status", "visibility", "created_by", "created_at")
    list_filter = ("status", "visibility", "created_at")
    search_fields = ("title", "summary")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)

    fieldsets = (
        (None, {"fields": ("id", "title", "summary")}),
        ("Status", {"fields": ("status", "visibility")}),
        ("Ownership", {"fields": ("created_by",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
