from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel


# Allowed models for FeatureLink (app_label.model_name)
ALLOWED_LINK_MODELS = frozenset([
    "geostories.geostory",
    "calendarevents.calendarevent",  # Future
    "geofeedback.geofeedback",       # Future
])


class FeatureLink(TimeStampedModel):
    """
    Polymorphic link between two features (e.g. GeoStory -> GeoStory).
    
    Only GeoStory, CalendarEvent, and GeoFeedback can be linked.
    GeoContext is NOT linkable (it's a submodule of features).
    """

    class LinkType(models.TextChoices):
        DIRECT = "direct", "Direct Link"
        READ_MORE = "read_more", "Read More"
        ACTION = "action", "Action"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.CASCADE,
        related_name="feature_links"
    )
    
    link_type = models.CharField(
        max_length=20,
        choices=LinkType.choices,
        default=LinkType.DIRECT
    )

    # Source (The entity linking TO something)
    source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="feature_link_sources"
    )
    source_object_id = models.UUIDField()
    source_object = GenericForeignKey("source_content_type", "source_object_id")

    # Target (The entity being linked TO)
    target_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="feature_link_targets"
    )
    target_object_id = models.UUIDField()
    target_object = GenericForeignKey("target_content_type", "target_object_id")

    # Who created the link
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="feature_links",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Feature Link"
        verbose_name_plural = "Feature Links"
        indexes = [
            models.Index(fields=["source_content_type", "source_object_id"]),
            models.Index(fields=["target_content_type", "target_object_id"]),
        ]
        constraints = [
            # Prevent duplicate links
            models.UniqueConstraint(
                fields=[
                    "source_content_type",
                    "source_object_id",
                    "target_content_type",
                    "target_object_id",
                ],
                name="unique_feature_link",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_object} -> {self.target_object} ({self.link_type})"

    def _get_model_key(self, content_type: ContentType) -> str:
        """Return app_label.model format for a ContentType."""
        return f"{content_type.app_label}.{content_type.model}"

    def clean(self) -> None:
        """Validate the link."""
        errors = {}

        # 1. Validate source content type is allowed
        if self.source_content_type_id:
            source_key = self._get_model_key(self.source_content_type)
            if source_key not in ALLOWED_LINK_MODELS:
                errors["source_content_type"] = (
                    f"'{source_key}' is not allowed. "
                    f"Only GeoStory, CalendarEvent, and GeoFeedback can be linked."
                )

        # 2. Validate target content type is allowed
        if self.target_content_type_id:
            target_key = self._get_model_key(self.target_content_type)
            if target_key not in ALLOWED_LINK_MODELS:
                errors["target_content_type"] = (
                    f"'{target_key}' is not allowed. "
                    f"Only GeoStory, CalendarEvent, and GeoFeedback can be linked."
                )

        # 3. Self-link check
        if (
            self.source_content_type == self.target_content_type
            and self.source_object_id == self.target_object_id
        ):
            errors["target_object_id"] = "Cannot link an object to itself."

        # 4. Campaign boundary check
        # Both source and target must belong to the same campaign as this link
        # Note: Check campaign_id first to avoid RelatedObjectDoesNotExist on unsaved instances
        if self.campaign_id and self.source_object and hasattr(self.source_object, "campaign"):
            if self.source_object.campaign_id != self.campaign_id:
                errors["source_object_id"] = (
                    "Source object must belong to the same campaign."
                )

        if self.campaign_id and self.target_object and hasattr(self.target_object, "campaign"):
            if self.target_object.campaign_id != self.campaign_id:
                errors["target_object_id"] = (
                    "Target object must belong to the same campaign."
                )

        if errors:
            raise ValidationError(errors)

        super().clean()

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)
