"""
GeoFeedback & FeedbackSubmission models.

GeoFeedback represents a configurable feedback campaign that can collect
star ratings, custom form responses (via django-basic-form-builder), and
spatial drawings from citizens. Each GeoFeedback belongs to a Campaign
and can be linked to map layers for contextual display.

FeedbackSubmission stores individual citizen responses to a GeoFeedback,
including optional ratings, dynamic form answers (JSONB), and spatial
drawings (GeometryField).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.gis.db import models as gis_models
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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

    # Reverse generic relations for cascading deletes of FeatureLinks
    feature_links_source = GenericRelation(
        "featurelinks.FeatureLink",
        content_type_field="source_content_type",
        object_id_field="source_object_id",
        related_query_name="geofeedback_source"
    )
    feature_links_target = GenericRelation(
        "featurelinks.FeatureLink",
        content_type_field="target_content_type",
        object_id_field="target_object_id",
        related_query_name="geofeedback_target"
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


class FeedbackSubmission(TimeStampedModel):
    """
    An individual citizen response to a GeoFeedback campaign.

    Attributes:
        id: UUID primary key
        feedback: Parent GeoFeedback this submission belongs to
        submitted_by: Optional FK to User (nullable for anonymous submissions)
        rating: Star rating 1-5 (nullable, required when feedback.rating_enabled)
        form_data: JSONB storing dynamic form answers (nullable)
        geometry: Mixed geometry for drawings (Point/Line/Polygon, nullable)
        is_anonymized: Whether PII has been stripped from this submission
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    feedback = models.ForeignKey(
        GeoFeedback,
        on_delete=models.CASCADE,
        related_name="submissions",
    )

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback_submissions",
    )

    rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Star rating from 1 to 5.",
    )

    form_data = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text="Dynamic form answers stored as JSON (from formbuilder).",
    )

    geometry = gis_models.GeometryField(
        srid=4326,
        null=True,
        blank=True,
        help_text="Spatial drawing (Point, LineString, or Polygon).",
    )

    is_anonymized = models.BooleanField(
        default=False,
        help_text="Whether personally identifiable information has been removed.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Feedback Submission"
        verbose_name_plural = "Feedback Submissions"
        indexes = [
            models.Index(fields=["feedback"]),
            models.Index(fields=["submitted_by"]),
        ]

    def __str__(self) -> str:
        user_label = self.submitted_by or "Anonymous"
        rating_label = f"★{self.rating}" if self.rating else "no rating"
        return f"Submission to {self.feedback} ({user_label}, {rating_label})"

    def clean(self) -> None:
        """Validate submission against parent GeoFeedback configuration."""
        errors = {}

        # Rating validation
        if self.rating is not None:
            if self.rating < 1 or self.rating > 5:
                errors["rating"] = "Rating must be between 1 and 5."

        # Geometry only if allow_drawings is enabled
        if self.geometry and self.feedback_id:
            try:
                if not self.feedback.allow_drawings:
                    errors["geometry"] = (
                        "This feedback does not accept spatial drawings."
                    )
            except GeoFeedback.DoesNotExist:
                pass  # FK will be validated by Django

        if errors:
            raise ValidationError(errors)

        super().clean()

    def save(self, *args, **kwargs) -> None:
        """Override save to validate."""
        self.full_clean()
        super().save(*args, **kwargs)
