"""
GeoContext model - Shared Editor.js content block.

GeoContext holds canonical Editor.js JSON content that can be linked to
features like GeoStory, Event, or GeoFeedback. Content is stored as a
structured JSON document rather than freeform text or HTML.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from tosca_api.apps.core.editorjs import (
    empty_document,
    validate_and_normalize,
)
from tosca_api.apps.core.models import TimeStampedModel


def empty_editorjs_document() -> dict:
    """Return the canonical empty Editor.js document."""
    return empty_document()


class GeoContext(TimeStampedModel):
    """
    Shared Editor.js content block model.

    Stores canonical Editor.js JSON that can be linked to feature models
    (GeoStory, Event, GeoFeedback). Deep validation and normalization of
    block structure is handled by the Editor.js layer (see Task 7.2);
    this model only guarantees that empty content is represented as
    ``{"blocks": []}`` rather than ``None``.

    Attributes:
        id: UUID primary key
        content: Canonical Editor.js JSON document
        created_by: The user who created this content block
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text=(
            "Human-readable label used in admin dropdowns where GeoContext "
            "rows are referenced (GeoStory / Event / GeoFeedback). Falls "
            "back to a derived excerpt when left blank, but setting it "
            "explicitly keeps related-object pickers usable."
        ),
    )
    content = models.JSONField(default=empty_editorjs_document, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="geocontexts",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "GeoContext"
        verbose_name_plural = "GeoContexts"

    def __str__(self) -> str:
        """
        Dropdown-friendly label.

        Picks, in order: an explicit ``title``, a truncated excerpt from the
        first header / paragraph block, or a short identifier fallback. The
        block-count suffix is retained so editors can still tell rich rows
        apart from empty ones at a glance.
        """
        title = (self.title or "").strip()
        blocks = (self.content or {}).get("blocks") or []
        suffix = f" ({len(blocks)} block(s))" if blocks else " (empty)"

        if title:
            return f"{title}{suffix}"

        excerpt = self._derive_excerpt(blocks)
        if excerpt:
            return f"{excerpt}{suffix}"

        short_id = str(self.id)[:8] if self.id else "new"
        return f"GeoContext {short_id}{suffix}"

    @staticmethod
    def _derive_excerpt(blocks: list, max_len: int = 60) -> str:
        """Pull a short plain-text excerpt from the first text-bearing block."""
        for block in blocks:
            if not isinstance(block, dict):
                continue
            data = block.get("data") or {}
            if block.get("type") in ("header", "paragraph", "quote"):
                text = str(data.get("text") or "").strip()
                if text:
                    return text[:max_len] + ("…" if len(text) > max_len else "")
        return ""

    def save(self, *args, **kwargs) -> None:
        """Validate and normalize Editor.js content before persistence."""
        self.content = validate_and_normalize(self.content)
        super().save(*args, **kwargs)
