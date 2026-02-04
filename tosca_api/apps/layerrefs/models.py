"""
LayerRef model - Pointer to GeoServer layers.

This model serves as a reference to layers published in GeoServer.
It allows Many-to-Many relationships between application features (GeoStories, etc.)
and actual map layers without duplicating layer configuration.
"""

from __future__ import annotations

import uuid

from django.db import models

from tosca_api.apps.core.models import TimeStampedModel
from tosca_api.apps.core.sanitization import sanitize_simple


class LayerRef(TimeStampedModel):
    """
    Reference to a GeoServer layer.

    Attributes:
        id: UUID primary key
        layer_name: The unique identifier of the layer in GeoServer (e.g., 'workspace:layername')
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    layer_name = models.CharField(max_length=255, unique=True, db_index=True)

    class Meta:
        ordering = ["layer_name"]
        verbose_name = "Layer Reference"
        verbose_name_plural = "Layer References"

    def __str__(self) -> str:
        return self.layer_name

    def save(self, *args, **kwargs) -> None:
        """Override save to enforce Zero Trust sanitization on layer_name."""
        self.layer_name = sanitize_simple(self.layer_name)
        super().save(*args, **kwargs)
