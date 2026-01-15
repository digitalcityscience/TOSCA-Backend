from __future__ import annotations

from django.conf import settings
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel


class Layer(TimeStampedModel):
    """Represents a geospatial layer served to the frontend."""

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="layers",
    )
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:  # pragma: no cover
        return self.name
