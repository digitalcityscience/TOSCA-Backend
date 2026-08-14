"""
GeoStory model - The core narrative unit.

A GeoStory combines a rich text narrative (GeoContext) with map layers
(geodata_providers.Layer) and is organized within a Campaign.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from django.db.models.fields.files import ImageField, ImageFieldFile, ImageFileDescriptor
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel
from tosca_api.apps.core.sanitization import sanitize_simple


def geostory_hero_image_upload_to(instance: "GeoStory", filename: str) -> str:
    """Store hero images under a stable GeoStory UUID scope."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    unique_filename = f"{uuid.uuid4().hex}.{extension}"
    return f"geostories/{instance.pk}/hero/{unique_filename}"


class HeroImageFieldFile(ImageFieldFile):
    """ImageField file whose backend follows GeoStory's storage alias."""

    def refresh_storage(self) -> None:
        alias = getattr(
            self.instance,
            "hero_image_storage_alias",
            GeoStory.StorageAlias.DEFAULT,
        ) or GeoStory.StorageAlias.DEFAULT
        self.storage = storages[alias]

    def __init__(self, instance, field, name):
        super().__init__(instance, field, name)
        self.refresh_storage()


class HeroImageFileDescriptor(ImageFileDescriptor):
    """Keep a cached FieldFile aligned after the alias changes."""

    def __get__(self, instance, cls=None):
        file = super().__get__(instance, cls)
        if instance is not None and isinstance(file, HeroImageFieldFile):
            file.refresh_storage()
        return file


class HeroImageField(ImageField):
    """ImageField backed by the GeoStory-selected Django storage alias."""

    attr_class = HeroImageFieldFile
    descriptor_class = HeroImageFileDescriptor


class GeoStoryQuerySet(models.QuerySet):
    def published(self):
        """Stories visible to a reader with no elevated access: published only.

        GeoStory has no separate public/private flag (unlike Event and
        GeoFeedback) — status is the only visibility axis.
        """
        return self.filter(status=GeoStory.Status.PUBLISHED)


class GeoStory(TimeStampedModel):
    """
    A specific story or narrative attached to a location/map view.

    Attributes:
        id: UUID primary key
        title: Headline of the story (sanitized)
        summary: Brief intro/description (sanitized)
        status: Draft/Published/Archived
        campaign: The parent campaign this story belongs to
        author: The creator/owner
        context: 1:1 link to the rich content content block
        layers: M2M link to map layers
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)

    objects = GeoStoryQuerySet.as_manager()
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    class StorageAlias(models.TextChoices):
        DEFAULT = "default", "Private (default)"
        PUBLIC = "media_public", "Public"
        ARCHIVE = "media_archive", "Archive"

    hero_image_storage_alias = models.CharField(
        max_length=20,
        choices=StorageAlias.choices,
        default=StorageAlias.DEFAULT,
        help_text="Storage alias currently holding hero_image; maintained by the media lifecycle.",
    )
    hero_image = HeroImageField(
        upload_to=geostory_hero_image_upload_to,
        null=True,
        blank=True,
    )
    hero_image_alt = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.CASCADE,
        related_name="geostories",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="geostories",
    )
    context = models.OneToOneField(
        "geocontext.GeoContext",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="geostory",
    )
    layers = models.ManyToManyField(
        "geodata_providers.Layer",
        through="GeoStoryLayer",
        related_name="geostories",
        blank=True,
    )
    
    # Reverse generic relations for cascading deletes of FeatureLinks
    feature_links_source = GenericRelation(
        "featurelinks.FeatureLink",
        content_type_field="source_content_type",
        object_id_field="source_object_id",
        related_query_name="geostory_source"
    )
    feature_links_target = GenericRelation(
        "featurelinks.FeatureLink",
        content_type_field="target_content_type",
        object_id_field="target_object_id",
        related_query_name="geostory_target"
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "GeoStory"
        verbose_name_plural = "GeoStories"

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        """Require descriptive alt text whenever a hero image is present."""
        super().clean()
        if self.hero_image and not (self.hero_image_alt or "").strip():
            raise ValidationError(
                {
                    "hero_image_alt": (
                        "Hero image alt text is required when a hero image is set."
                    )
                }
            )

    def save(self, *args, **kwargs) -> None:
        """Override save to enforce Zero Trust sanitization."""
        self.title = sanitize_simple(self.title)
        self.summary = sanitize_simple(self.summary)
        self.hero_image_alt = sanitize_simple(self.hero_image_alt)

        # New/replaced uploads must be written directly to the bucket dictated
        # by the current ownership state. Status/visibility-only saves are
        # intentionally left to MediaLifecycleService, which performs the
        # copy/update/delete sequence for an already-committed object.
        hero_file = self.__dict__.get("hero_image")
        if hero_file and (self._state.adding or not getattr(hero_file, "_committed", True)):
            self.hero_image_storage_alias = self.desired_hero_image_storage_alias()
            if isinstance(hero_file, HeroImageFieldFile):
                # The descriptor may have created the FieldFile before the
                # alias was calculated. Refresh now so FileField.pre_save()
                # writes the upload to the selected bucket, not the default.
                hero_file.refresh_storage()
        super().save(*args, **kwargs)

    def desired_hero_image_storage_alias(self) -> str:
        """Return the current lifecycle bucket for a newly saved hero image."""
        if not self.campaign_id:
            return self.StorageAlias.DEFAULT
        campaign = self.campaign
        if (
            campaign.status == campaign.Status.ARCHIVED
            or self.status == self.Status.ARCHIVED
        ):
            return self.StorageAlias.ARCHIVE
        if campaign.visibility == campaign.Visibility.PUBLIC:
            return self.StorageAlias.PUBLIC
        return self.StorageAlias.DEFAULT


class GeoStoryLayer(TimeStampedModel):
    """
    Through model for GeoStory <-> geodata_providers.Layer.

    Allows ordering of layers within a story. Layers must be public and
    published — see ``clean()``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    geostory = models.ForeignKey(GeoStory, on_delete=models.CASCADE)
    layer = models.ForeignKey(
        "geodata_providers.Layer",
        on_delete=models.CASCADE,
        related_name="geostory_uses",
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["geostory", "layer"], name="geostories_geostorylayer_uniq"
            ),
        ]
        verbose_name = "GeoStory Layer"
        verbose_name_plural = "GeoStory Layers"

    def __str__(self) -> str:
        return f"{self.geostory} - {self.layer} ({self.display_order})"

    def clean(self) -> None:
        """Reject non-public or non-published layer assignments."""
        from tosca_api.apps.geodata_providers.validators import (
            validate_layer_is_public_and_published,
        )

        super().clean()
        if self.layer_id is not None:
            validate_layer_is_public_and_published(self.layer)

    def save(self, *args, **kwargs) -> None:
        """
        Override save to auto-increment display_order if not specified
        and to enforce public + published layer validation.
        """
        if self._state.adding and self.display_order == 0:
            # Find the current maximum order for this story
            max_order = (
                GeoStoryLayer.objects.filter(geostory=self.geostory).aggregate(
                    models.Max("display_order")
                )["display_order__max"]
            )
            if max_order is not None:
                self.display_order = max_order + 1
        self.full_clean()
        super().save(*args, **kwargs)
