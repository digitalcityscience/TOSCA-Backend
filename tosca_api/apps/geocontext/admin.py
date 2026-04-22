from django.contrib import admin

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
class GeoContextAdmin(admin.ModelAdmin):
    """Admin interface for GeoContext with Editor.js authoring."""

    form = GeoContextAdminForm

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
        """Return a truncated plain-text preview of Editor.js content."""
        text = _extract_plain_text(obj.content)
        if not text:
            return "(empty)"
        if len(text) > _PREVIEW_MAX:
            return text[:_PREVIEW_MAX] + "..."
        return text
