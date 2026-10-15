from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models

from tosca_api.apps.core.editorjs import empty_document, validate_and_normalize
from tosca_api.apps.core.models import TimeStampedModel


# Soft thresholds: above these the frontend is likely to wrap logos or move
# documents into an overflow menu. Editors are warned, never blocked.
SOFT_LIMIT_LOGOS = 8
SOFT_LIMIT_DOCUMENTS = 6


def footer_logo_upload_to(instance: "FooterLogo", filename: str) -> str:
    extension = Path(filename).suffix.lower()
    return f"footer/{instance.footer_id}/logos/{uuid.uuid4().hex}{extension}"


class Footer(TimeStampedModel):
    """An editable global footer; publishing stores an immutable public snapshot."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=120, unique=True)
    is_published = models.BooleanField(default=False, editable=False, db_index=True)
    publication_version = models.PositiveBigIntegerField(default=0, editable=False)
    published_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="published_footers",
    )

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_published"],
                condition=models.Q(is_published=True),
                name="one_published_footer",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def delete(self, *args, **kwargs):
        if self.is_published:
            raise ValidationError("Publish another footer before deleting this one.")
        return super().delete(*args, **kwargs)


class FooterLogo(TimeStampedModel):
    footer = models.ForeignKey(Footer, on_delete=models.CASCADE, related_name="logos")
    image = models.ImageField(
        upload_to=footer_logo_upload_to,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp"])],
    )
    alt_text = models.CharField(max_length=255)
    destination_url = models.URLField(max_length=500, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["footer", "display_order"],
                name="unique_footer_logo_order",
                deferrable=models.Deferrable.DEFERRED,
            )
        ]

    def __str__(self) -> str:
        return self.alt_text

    def clean(self) -> None:
        super().clean()
        self.alt_text = (self.alt_text or "").strip()
        if not self.alt_text:
            raise ValidationError({"alt_text": "Alternative text is required."})


class FooterDocument(TimeStampedModel):
    footer = models.ForeignKey(Footer, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=120)
    slug = models.SlugField(
        max_length=80,
        validators=[
            RegexValidator(
                regex=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
                message="Use lowercase letters, numbers, and single hyphens only.",
            )
        ],
        help_text="Reserved frontend path without a leading slash, for example privacy.",
    )
    content = models.JSONField(default=empty_document, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["footer", "slug"], name="unique_footer_document_slug"),
            models.UniqueConstraint(
                fields=["footer", "display_order"],
                name="unique_footer_document_order",
                deferrable=models.Deferrable.DEFERRED,
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        super().clean()
        self.title = (self.title or "").strip()
        if not self.title:
            raise ValidationError({"title": "A document title is required."})
        self.content = validate_and_normalize(self.content)

    def save(self, *args, **kwargs) -> None:
        self.content = validate_and_normalize(self.content)
        super().save(*args, **kwargs)
