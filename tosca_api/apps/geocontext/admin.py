from django.contrib import admin

from tosca_api.apps.organizations.permissions import PlatformOnlyChangeDeleteMixin

from .forms import GeoContextAdminForm
from .models import GeoContext

_PREVIEW_MAX = 75


def _extract_plain_text(content) -> str:
    """Flatten block JSON into a single whitespace-joined plain-text string."""
    if not isinstance(content, dict):
        return ""
    blocks = content.get("blocks") or []
    chunks: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        data = block.get("data") or {}
        block_type = block.get("type")
        if block_type in ("paragraph", "header", "quote"):
            chunks.append(str(data.get("text") or ""))
        elif block_type == "code":
            chunks.append(str(data.get("code") or ""))
        elif block_type == "list":
            for item in data.get("items") or []:
                if isinstance(item, dict):
                    chunks.append(str(item.get("content") or ""))
                elif isinstance(item, str):
                    chunks.append(item)
    text = " ".join(c.strip() for c in chunks if c and c.strip())
    return text


@admin.register(GeoContext)
class GeoContextAdmin(PlatformOnlyChangeDeleteMixin, admin.ModelAdmin):
    """Admin interface for GeoContext with Editor.js authoring.

    GeoContext has no owning org -- ``Event.context``/``EventSeries.default_context``
    are plain (non-unique) FKs, so one row can be referenced by events across
    different organizations (security tickets 2026-08-19 ticket 05). No
    coherent org-scoped queryset exists for it, so change/delete are locked
    to superuser instead of the normal WRITER/ADMIN ladder; API-side writes
    (nested authoring flows) are unaffected -- this only restricts the
    admin UI.
    """

    form = GeoContextAdminForm

    list_display = ("title_or_excerpt", "content_preview", "created_by", "created_at")
    # created_by is a FK shown directly in list_display; make the join
    # explicit rather than relying on Django's implicit changelist
    # optimization for FK columns (see test_changelist_query_count_does_not_grow_with_row_count).
    list_select_related = ("created_by",)
    list_filter = ("created_at",)
    search_fields = ("id", "title")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)

    fieldsets = (
        (None, {"fields": ("id", "title", "content")}),
        ("Ownership", {"fields": ("created_by",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Title", ordering="title")
    def title_or_excerpt(self, obj: GeoContext) -> str:
        """Show the explicit title, or the derived excerpt fallback."""
        return str(obj)

    @admin.display(description="Content Preview")
    def content_preview(self, obj: GeoContext) -> str:
        """Return a truncated plain-text preview of Editor.js content."""
        text = _extract_plain_text(obj.content)
        if not text:
            return "(empty)"
        if len(text) > _PREVIEW_MAX:
            return text[:_PREVIEW_MAX] + "..."
        return text
