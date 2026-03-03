"""
GeoFeedback model - Citizen feedback collection with forms and ratings.

GeoFeedback represents a configurable feedback campaign that can collect
star ratings, custom form responses (via django-basic-form-builder), and
spatial drawings from citizens. Each GeoFeedback belongs to a Campaign
and can be linked to map layers for contextual display.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel
from tosca_api.apps.core.sanitization import sanitize_simple


class GeoFeedback(TimeStampedModel):
    """
    A feedback collection point within a campaign.

    Attributes:
        id: UUID primary key
        campaign: Parent campaign
        title: Feedback title (sanitized)
        description: Brief description (sanitized)
        context: 1:1 link to rich content block (optional)
        custom_form: Optional link to a formbuilder CustomForm
        rating_enabled: Whether star ratings are collected
        form_enabled: Whether form submissions are collected
        allow_drawings: Whether spatial drawings are accepted
        status: Draft/Published/Closed
        visibility: Public/Private
        created_by: User who created this feedback
        layers: M2M link to map layers
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CLOSED = "closed", "Closed"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.CASCADE,
        related_name="feedbacks",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    context = models.OneToOneField(
        "geocontext.GeoContext",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback",
    )

    custom_form = models.ForeignKey(
        "formbuilder.CustomForm",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedbacks",
        help_text="Link to a form created with django-basic-form-builder.",
    )

    rating_enabled = models.BooleanField(
        default=True,
        help_text="Enable star rating collection.",
    )
    form_enabled = models.BooleanField(
        default=False,
        help_text="Enable custom form submission.",
    )
    allow_drawings = models.BooleanField(
        default=False,
        help_text="Allow users to submit spatial drawings.",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_feedbacks",
    )

    layers = models.ManyToManyField(
        "layerrefs.LayerRef",
        through="FeedbackLayer",
        related_name="feedbacks",
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "GeoFeedback"
        verbose_name_plural = "GeoFeedbacks"
        indexes = [
            models.Index(fields=["campaign"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        """Validate the feedback configuration."""
        errors = {}

        # At least one of rating_enabled or form_enabled must be True
        if not self.rating_enabled and not self.form_enabled:
            errors["rating_enabled"] = (
                "At least one of rating or form must be enabled."
            )
            errors["form_enabled"] = (
                "At least one of rating or form must be enabled."
            )

        # If form is enabled, custom_form must be set
        if self.form_enabled and not self.custom_form_id:
            errors["custom_form"] = (
                "A custom form must be linked when form submission is enabled."
            )

        if errors:
            raise ValidationError(errors)

        super().clean()

    def save(self, *args, **kwargs) -> None:
        """Override save to sanitize inputs and validate."""
        self.title = sanitize_simple(self.title)
        self.description = sanitize_simple(self.description)
        self.full_clean()
        super().save(*args, **kwargs)


class FeedbackLayer(models.Model):
    """
    Through model for GeoFeedback <-> LayerRef.
    Allows ordering of layers within a feedback.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    feedback = models.ForeignKey(GeoFeedback, on_delete=models.CASCADE)
    layer = models.ForeignKey("layerrefs.LayerRef", on_delete=models.CASCADE)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "created_at"]
        unique_together = ("feedback", "layer")
        verbose_name = "Feedback Layer"
        verbose_name_plural = "Feedback Layers"

    def __str__(self) -> str:
        return f"{self.feedback} - {self.layer} ({self.display_order})"

    def save(self, *args, **kwargs) -> None:
        """Auto-increment display_order if not specified."""
        if self._state.adding and self.display_order == 0:
            max_order = (
                FeedbackLayer.objects.filter(feedback=self.feedback).aggregate(
                    models.Max("display_order")
                )["display_order__max"]
            )
            if max_order is not None:
                self.display_order = max_order + 1
        super().save(*args, **kwargs)
