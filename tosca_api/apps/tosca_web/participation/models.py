from __future__ import annotations

from django.conf import settings
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel


class ParticipationForm(TimeStampedModel):
    """Citizen participation form submissions."""

    title = models.CharField(max_length=255)
    description = models.TextField()
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="participation_forms",
    )
    payload = models.JSONField(default=dict)

    def __str__(self) -> str:  # pragma: no cover
        return self.title
