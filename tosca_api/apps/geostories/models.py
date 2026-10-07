"""
GeoStory model - The core narrative unit.

A GeoStory combines an owned rich-content document with map scenes
(camera + geodata_providers.Layer selections) and is organized within a Campaign.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models.fields.files import ImageField, ImageFieldFile, ImageFileDescriptor
from django.db import models

from tosca_api.apps.core.editorjs import empty_document, validate_and_normalize
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
        alias = (
            getattr(
                self.instance,
                "hero_image_storage_alias",
                GeoStory.StorageAlias.DEFAULT,
            )
            or GeoStory.StorageAlias.DEFAULT
        )
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
        about_author: Short biography for the author section (sanitized)
        status: Draft/Published/Archived
        campaign: The parent campaign this story belongs to
        author: The creator/owner
        content: The canonical Editor.js document containing the story
        scenes: Ordered map scenes (see GeoStoryScene)
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)

    objects = GeoStoryQuerySet.as_manager()
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    about_author = models.TextField(
        blank=True,
        default="",
        verbose_name="About the Author/Transparency (Public)",
        help_text=(
            "Here you can provide information about yourself and your perspective "
            "(e.g. your occupation, where you live, and the narrative background "
            "or paradigm of your Geostory)."
        ),
    )
    content = models.JSONField(
        default=empty_document,
        blank=True,
        help_text="The story body as a canonical Editor.js document.",
    )

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
    # Reverse generic relations for cascading deletes of FeatureLinks
    feature_links_source = GenericRelation(
        "featurelinks.FeatureLink",
        content_type_field="source_content_type",
        object_id_field="source_object_id",
        related_query_name="geostory_source",
    )
    feature_links_target = GenericRelation(
        "featurelinks.FeatureLink",
        content_type_field="target_content_type",
        object_id_field="target_object_id",
        related_query_name="geostory_target",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "GeoStory"
        verbose_name_plural = "GeoStories"

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        """Validate owned content and require alt text for the hero image."""
        super().clean()
        errors = {}
        try:
            self.content = validate_and_normalize(self.content)
        except ValidationError as exc:
            errors["content"] = exc.messages
        if self.hero_image and not (self.hero_image_alt or "").strip():
            errors["hero_image_alt"] = "Hero image alt text is required when a hero image is set."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        """Override save to enforce Zero Trust sanitization."""
        self.title = sanitize_simple(self.title)
        self.summary = sanitize_simple(self.summary)
        self.about_author = sanitize_simple(self.about_author)
        self.hero_image_alt = sanitize_simple(self.hero_image_alt)
        self.content = validate_and_normalize(self.content)

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
        """Return the current lifecycle bucket for a newly saved hero image.

        Security tickets S2: public only when the campaign is public **and**
        this story is itself published -- a draft story under a public
        campaign must stay private (previously campaign visibility alone
        decided it, which put a public campaign's draft-story hero images in
        the unsigned public bucket).
        """
        if not self.campaign_id:
            return self.StorageAlias.DEFAULT
        campaign = self.campaign
        if campaign.status == campaign.Status.ARCHIVED or self.status == self.Status.ARCHIVED:
            return self.StorageAlias.ARCHIVE
        if (
            campaign.visibility == campaign.Visibility.PUBLIC
            and self.status == self.Status.PUBLISHED
        ):
            return self.StorageAlias.PUBLIC
        return self.StorageAlias.DEFAULT


class GeoStoryLayer(TimeStampedModel):
    """
    Legacy story-level layer assignment, superseded by scenes.

    Rows were copied into each story's "Overview" scene (migration 0012) and
    are no longer read or written. Kept for one release as a rollback net;
    dropped in Task 10.9.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    geostory = models.ForeignKey(GeoStory, on_delete=models.CASCADE)
    layer = models.ForeignKey(
        "geodata_providers.Layer",
        on_delete=models.CASCADE,
        related_name="geostory_uses",
    )
    # RESTRICT, not PROTECT: a style still in use cannot be removed on its
    # own, but deleting the whole layer (which cascades to both the style
    # assignment and this row) must go through.
    style_assignment = models.ForeignKey(
        "geodata_providers.LayerStyleAssignment",
        on_delete=models.RESTRICT,
        related_name="geostory_uses",
        null=True,
        blank=True,
        help_text="Pinned style assignment; defaults to the layer's active default assignment.",
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
        errors = {}
        if self.layer_id is not None:
            validate_layer_is_public_and_published(self.layer)
            if self.style_assignment_id is None:
                from tosca_api.apps.geodata_providers.models import LayerStyleAssignment

                self.style_assignment = self.layer.style_assignments.filter(
                    role=LayerStyleAssignment.Role.DEFAULT,
                    is_active=True,
                ).first()
        if (
            self.layer_id
            and self.style_assignment_id
            and self.style_assignment.layer_id != self.layer_id
        ):
            errors["style_assignment"] = "Style assignment must belong to the selected layer."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        """
        Override save to auto-increment display_order if not specified
        and to enforce public + published layer validation.
        """
        if self._state.adding and self.display_order == 0:
            # Find the current maximum order for this story
            max_order = GeoStoryLayer.objects.filter(geostory=self.geostory).aggregate(
                models.Max("display_order")
            )["display_order__max"]
            if max_order is not None:
                self.display_order = max_order + 1
        self.full_clean()
        super().save(*args, **kwargs)


MAX_SCENE_FEATURE_IDS = 500


class GeoStoryScene(TimeStampedModel):
    """
    A captured map state that the story switches to while the reader scrolls.

    The camera is optional: a scene without a center fits the map to its
    layers (or to ``bounds`` when captured).
    """

    class Transition(models.TextChoices):
        FLY = "fly", "Fly"
        EASE = "ease", "Ease"
        JUMP = "jump", "Jump"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    geostory = models.ForeignKey(GeoStory, on_delete=models.CASCADE, related_name="scenes")
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255)
    caption = models.TextField(blank=True, default="")

    center_lng = models.FloatField(
        null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)]
    )
    center_lat = models.FloatField(
        null=True, blank=True, validators=[MinValueValidator(-90), MaxValueValidator(90)]
    )
    zoom = models.FloatField(
        null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(24)]
    )
    bearing = models.FloatField(
        default=0, validators=[MinValueValidator(-180), MaxValueValidator(180)]
    )
    pitch = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(85)])
    bounds = models.JSONField(
        null=True,
        blank=True,
        help_text="Captured viewport as [west, south, east, north] in EPSG:4326.",
    )
    transition = models.CharField(
        max_length=10, choices=Transition.choices, default=Transition.FLY
    )
    duration_ms = models.PositiveIntegerField(default=1500, validators=[MaxValueValidator(10000)])

    class Meta:
        ordering = ["order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["geostory", "order"], name="geostories_scene_unique_order"
            ),
        ]
        verbose_name = "GeoStory Scene"
        verbose_name_plural = "GeoStory Scenes"

    def __str__(self) -> str:
        return f"{self.geostory} - {self.title}"

    def clean(self) -> None:
        super().clean()
        errors = {}
        camera = (self.center_lng, self.center_lat, self.zoom)
        if any(value is not None for value in camera) and any(value is None for value in camera):
            errors["zoom"] = "Center longitude, center latitude and zoom must be set together."
        if self.bounds is not None:
            bounds_error = _validate_bounds(self.bounds)
            if bounds_error:
                errors["bounds"] = bounds_error
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.title = sanitize_simple(self.title)
        self.caption = sanitize_simple(self.caption)
        if self._state.adding and self.order == 0 and self.geostory_id:
            max_order = GeoStoryScene.objects.filter(geostory_id=self.geostory_id).aggregate(
                models.Max("order")
            )["order__max"]
            if max_order is not None:
                self.order = max_order + 1
        self.full_clean()
        super().save(*args, **kwargs)


def _validate_bounds(bounds) -> str | None:
    if (
        not isinstance(bounds, list)
        or len(bounds) != 4
        or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bounds)
    ):
        return "Bounds must be a list of four numbers: [west, south, east, north]."
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180):
        return "Bounds longitudes must satisfy -180 <= west < east <= 180."
    if not (-90 <= south < north <= 90):
        return "Bounds latitudes must satisfy -90 <= south < north <= 90."
    return None


class GeoStorySceneLayer(TimeStampedModel):
    """
    A layer rendered in a scene, with a pinned style and optional feature selection.

    Vector layers with an MBStyle style are drawn from WMTS vector tiles and may
    restrict or highlight features by an attribute value. Raster (GeoTIFF)
    layers and SLD-styled vector layers are drawn by GeoServer as WMS images.
    """

    class SourceKind(models.TextChoices):
        CATALOG_LAYER = "catalog_layer", "Catalog layer"

    class FeatureMode(models.TextChoices):
        ALL = "all", "All features"
        ONLY = "only", "Only selected features"
        HIGHLIGHT = "highlight", "Highlight selected features"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    scene = models.ForeignKey(
        GeoStoryScene, on_delete=models.CASCADE, related_name="scene_layers"
    )
    source_kind = models.CharField(
        max_length=30, choices=SourceKind.choices, default=SourceKind.CATALOG_LAYER
    )
    layer = models.ForeignKey(
        "geodata_providers.Layer",
        on_delete=models.CASCADE,
        related_name="geostory_scene_uses",
    )
    # RESTRICT, not PROTECT: a style still in use cannot be removed on its
    # own, but deleting the whole layer (which cascades to both the style
    # assignment and this row) must go through.
    style_assignment = models.ForeignKey(
        "geodata_providers.LayerStyleAssignment",
        on_delete=models.RESTRICT,
        related_name="geostory_scene_uses",
        null=True,
        blank=True,
        help_text="Pinned style assignment; defaults to the layer's active default assignment.",
    )
    render_layer_ids = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Optional MBStyle layer IDs for this scene. "
            "Leave empty to use the pinned assignment's selected layer IDs."
        ),
    )
    display_order = models.PositiveIntegerField(default=0)
    opacity = models.FloatField(default=1.0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    feature_mode = models.CharField(
        max_length=20, choices=FeatureMode.choices, default=FeatureMode.ALL
    )
    feature_id_attribute = models.CharField(max_length=255, blank=True, default="")
    feature_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Values of the feature ID attribute to show or highlight.",
    )

    class Meta:
        ordering = ["display_order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["scene", "layer", "style_assignment"],
                name="geostories_scenelayer_unique_layer_style",
            ),
        ]
        verbose_name = "GeoStory Scene Layer"
        verbose_name_plural = "GeoStory Scene Layers"

    def __str__(self) -> str:
        return f"{self.scene} - {self.layer} ({self.display_order})"

    @property
    def is_raster(self) -> bool:
        from tosca_api.apps.geodata_providers.models import Store

        return self.layer.store.store_type == Store.StoreType.GEOTIFF

    @property
    def uses_vector_tiles(self) -> bool:
        """Client-side vector tiles (MBStyle); otherwise GeoServer draws WMS images."""
        from tosca_api.apps.geodata_providers.models import Style

        return (
            not self.is_raster
            and self.style_assignment_id is not None
            and self.style_assignment.style.format == Style.StyleFormat.MBSTYLE
        )

    @property
    def effective_style_layer_ids(self) -> list[str]:
        """Resolve a scene-specific rule selection over the assignment default."""
        if self.render_layer_ids:
            return self.render_layer_ids
        if self.style_assignment_id:
            return self.style_assignment.style_layer_ids
        return []

    def clean(self) -> None:
        from tosca_api.apps.geodata_providers.models import LayerStyleAssignment
        from tosca_api.apps.geodata_providers.validators import (
            validate_layer_is_public_and_published,
        )

        super().clean()
        errors: dict[str, str] = {}
        if self.layer_id is not None:
            try:
                validate_layer_is_public_and_published(self.layer)
            except ValidationError as exc:
                errors.update({key: msgs[0] for key, msgs in exc.message_dict.items()})
            if self.style_assignment_id is None:
                self.style_assignment = self.layer.style_assignments.filter(
                    role=LayerStyleAssignment.Role.DEFAULT,
                    is_active=True,
                ).first()
            errors.update(self._style_errors())
            errors.update(self._feature_errors())
        if errors:
            raise ValidationError(errors)

    def _style_errors(self) -> dict[str, str]:
        from tosca_api.apps.geodata_providers.models import Style

        ids = self.render_layer_ids
        if not isinstance(ids, list) or any(
            not isinstance(layer_id, str) or not layer_id.strip() for layer_id in ids
        ):
            return {"render_layer_ids": "Render layer IDs must be a list of non-empty strings."}
        if len(ids) != len(set(ids)):
            return {"render_layer_ids": "Render layer IDs cannot contain duplicates."}

        assignment = self.style_assignment
        if assignment is None:
            return {"style_assignment": "Layer has no active default style; select a style."}
        if assignment.layer_id != self.layer_id:
            return {"style_assignment": "Style assignment must belong to the selected layer."}
        if not assignment.is_active:
            return {"style_assignment": "Style assignment is inactive."}
        style = assignment.style
        if style.validation_state != Style.ValidationState.VALID:
            return {"style_assignment": "Selected style is not valid."}

        if self.is_raster and style.format != Style.StyleFormat.SLD:
            return {"style_assignment": "Raster layers require an SLD style."}
        if style.format != Style.StyleFormat.MBSTYLE:
            # SLD-styled vector data is rendered by GeoServer as WMS images.
            if ids:
                return {"render_layer_ids": "Only MBStyle styles can select render layer IDs."}
            return {}

        effective_ids = self.effective_style_layer_ids
        if not effective_ids:
            return {"render_layer_ids": "Select at least one MBStyle layer to render."}
        selected = assignment.selected_mbstyle_layers(effective_ids)
        if len(selected) != len(effective_ids):
            known = {style_layer.get("id") for style_layer in selected}
            missing = [layer_id for layer_id in effective_ids if layer_id not in known]
            return {"render_layer_ids": "Unknown MBStyle layer IDs: " + ", ".join(missing) + "."}
        for style_layer in selected:
            if style_layer.get("type") in {"background", "raster", "hillshade"}:
                return {
                    "render_layer_ids": (
                        f"MBStyle layer '{style_layer.get('id')}' cannot render a vector layer."
                    )
                }
        return {}

    def _feature_errors(self) -> dict[str, str]:
        ids = self.feature_ids
        if not isinstance(ids, list) or any(
            isinstance(value, bool) or not isinstance(value, (str, int, float)) for value in ids
        ):
            return {"feature_ids": "Feature IDs must be a list of strings or numbers."}
        if self.feature_mode == self.FeatureMode.ALL:
            if ids:
                return {"feature_ids": "Clear selected features or choose a selection mode."}
            return {}
        if not self.uses_vector_tiles:
            return {
                "feature_mode": (
                    "Feature selection needs a vector layer with an MBStyle style."
                )
            }
        if not self.feature_id_attribute.strip():
            return {"feature_id_attribute": "Choose the attribute that identifies features."}
        if not ids:
            return {"feature_ids": "Select at least one feature."}
        if len(ids) > MAX_SCENE_FEATURE_IDS:
            return {"feature_ids": f"Select at most {MAX_SCENE_FEATURE_IDS} features."}
        if len(ids) != len(set(ids)):
            return {"feature_ids": "Feature IDs cannot contain duplicates."}
        return {}

    def save(self, *args, **kwargs) -> None:
        if self._state.adding and self.display_order == 0 and self.scene_id:
            max_order = GeoStorySceneLayer.objects.filter(scene_id=self.scene_id).aggregate(
                models.Max("display_order")
            )["display_order__max"]
            if max_order is not None:
                self.display_order = max_order + 1
        self.full_clean()
        super().save(*args, **kwargs)
