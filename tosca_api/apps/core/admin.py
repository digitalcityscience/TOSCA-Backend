"""Admin registration for MediaAsset (epic-11 PR1 §3.3).

Read-only-ish: assets are created by upload endpoints
(geocontext.views, hero image saves), not through the admin form. The admin
page exists so operators can inspect ownership backfill results and manually
fix `campaign`/`owner_org` on assets the backfill couldn't match (see
``core.media_ownership`` for the matching strategy and its limits).
"""

from django.contrib import admin

from tosca_api.apps.organizations.permissions import OrgScopedAdminMixin

from .models import MediaAsset


@admin.register(MediaAsset)
class MediaAssetAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    """Admin interface for MediaAsset, scoped by ``owner_org``."""

    org_lookup = "owner_org__slug"

    list_display = (
        "storage_path",
        "owner_org",
        "campaign",
        "storage_alias",
        "mime",
        "size",
        "uploader",
        "created_at",
    )
    list_filter = ("owner_org", "storage_alias", "mime", "created_at")
    search_fields = ("storage_path", "original_name")
    readonly_fields = (
        "id",
        "storage_path",
        "original_name",
        "mime",
        "width",
        "height",
        "size",
        "uploader",
        "storage_alias",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    fieldsets = (
        (None, {"fields": ("id", "storage_path", "original_name")}),
        ("Ownership", {"fields": ("owner_org", "campaign", "uploader")}),
        ("Storage", {"fields": ("storage_alias",)}),
        ("Metadata", {"fields": ("mime", "width", "height", "size")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
