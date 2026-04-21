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

from tosca_api.apps.core.models import TimeStampedModel


def empty_editorjs_document() -> dict:
    """Return the canonical empty Editor.js document."""
    return {"blocks": []}


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
        blocks = (self.content or {}).get("blocks") or []
        if not blocks:
            return "GeoContext: (empty)"
        return f"GeoContext: {len(blocks)} block(s)"

    def save(self, *args, **kwargs) -> None:
        """Normalize missing/empty content to the canonical empty document."""
        if self.content in (None, "", {}, []):
            self.content = empty_editorjs_document()
        super().save(*args, **kwargs)
