"""
Geodata provider domain models.

This app coordinates remote geodata engines (GeoServer today, Martin and
pg_tileserv next) and the publishing pipeline that turns PostGIS tables
into served layers with associated styles. Models here are deliberately
opinionated about validation: each ``save`` runs ``full_clean`` so that
programmatic creates surface the same errors the admin would, and
cross-FK invariants are enforced in ``clean`` rather than left to forms.

Secrets (engine admin password, store password) are encrypted at write
time via :class:`EncryptedCharField` from ``encryption.py`` — that mixin
is unique to this app and is intentional, see ``encryption.py``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models, transaction
from PIL import Image, UnidentifiedImageError

from tosca_api.apps.core.editorjs import (
    description_document_from_text,
    description_document_to_text,
    empty_document,
    validate_description_document,
)
from tosca_api.apps.core.models import TimeStampedModel

from .encryption import EncryptedCharField


class SyncStateMixin(models.Model):
    """Common remote synchronization state for provider-owned resources."""

    class SyncState(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Unknown"
        SYNCED = "SYNCED", "Synced"
        LOCAL_ONLY = "LOCAL_ONLY", "Local only"
        REMOTE_ONLY = "REMOTE_ONLY", "Remote only"
        STALE = "STALE", "Stale"
        FAILED = "FAILED", "Failed"

    sync_state = models.CharField(
        max_length=20,
        choices=SyncState.choices,
        default=SyncState.LOCAL_ONLY,
        db_index=True,
        help_text="Current consistency state between Django and the remote provider.",
    )
    last_sync_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time this resource was successfully synchronized or checked.",
    )
    last_sync_error = models.TextField(
        blank=True,
        help_text="Most recent synchronization error, if any.",
    )
    remote_identifier = models.CharField(
        max_length=255,
        blank=True,
        help_text="Provider-side identifier for diagnostics, when available.",
    )
    remote_hash = models.CharField(
        max_length=128,
        blank=True,
        help_text="Provider-side version/hash for diagnostics, when available.",
    )

    class Meta:
        abstract = True

    @property
    def is_synced(self) -> bool:
        return self.sync_state == self.SyncState.SYNCED


class GeodataEngine(TimeStampedModel, EncryptedCharField):
    """
    Multi-engine geodata engine definition.

    Supports GeoServer, Martin, and pg_tileserv. ``base_url`` and the admin
    credentials describe how this app talks to the remote service; the
    credentials are encrypted on write via :class:`EncryptedCharField`.
    """

    class EngineType(models.TextChoices):
        GEOSERVER = "geoserver", "GeoServer"
        MARTIN = "martin", "Martin Tiles"
        PG_TILESERV = "pg_tileserv", "PostGIS TileServer"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Engine name (e.g., 'Default GeoServer', 'Production Martin')",
    )
    description = models.TextField(
        blank=True,
        help_text="Description of this geodata engine instance",
    )

    # Engine type and connection details
    engine_type = models.CharField(
        max_length=50,
        choices=EngineType.choices,
        default=EngineType.GEOSERVER,
        help_text="Type of geodata engine",
    )
    base_url = models.CharField(
        max_length=255,
        help_text="Backend-internal URL used by Django to connect to the engine",
    )
    public_url = models.CharField(
        max_length=255,
        help_text="Externally reachable URL exposed through public catalog bootstrap",
    )
    admin_username = models.CharField(
        max_length=100,
        default="admin2",
        blank=True,
        help_text="Admin username (if applicable)",
    )
    admin_password = models.CharField(
        max_length=100,
        default="geoserver2",
        blank=True,
        help_text="Admin password (if applicable)",
    )

    # Additional connection fields for different engine types
    api_key = models.CharField(
        max_length=255,
        blank=True,
        help_text="API key for engines that require it",
    )

    # Status
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Is this engine instance active?",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Is this the default engine instance?",
    )

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Geodata Provider"
        verbose_name_plural = "Geodata Providers"
        ordering = ["-is_default", "name"]

    def __str__(self) -> str:
        default_marker = " (Default)" if self.is_default else ""
        return f"{self.name}{default_marker}"

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        # Keep a single default engine — exclude self so re-saving the existing
        # default doesn't accidentally clear its own flag. Wrapped in atomic
        # so a failure between unsetting the old default and saving the new
        # one can't leave zero default engines.
        if self.is_default:
            with transaction.atomic():
                GeodataEngine.objects.exclude(pk=self.pk).filter(is_default=True).update(
                    is_default=False
                )
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    @property
    def decrypted_admin_password(self) -> str:
        """Return the decrypted admin password."""
        return self.decrypt_field("admin_password", self.admin_password)

    @property
    def engine_url(self) -> str:
        """Generic alias for the engine URL."""
        return self.base_url

    @property
    def geoserver_url(self) -> str:
        """Backward-compatible alias for existing GeoServer-centric code."""
        return self.base_url

    def get_client(self):
        """Return engine client instance from the client factory."""
        from .engine_factory import EngineClientFactory

        return EngineClientFactory.create_client(self)


class Workspace(SyncStateMixin, TimeStampedModel):
    """
    Logical grouping of data (e.g. 'mobility', 'environment').

    A workspace belongs to a specific :class:`GeodataEngine` and namespaces
    every store and layer published under it.
    """

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        PUBLIC = "public", "Public"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    geodata_engine = models.ForeignKey(
        GeodataEngine,
        on_delete=models.CASCADE,
        related_name="workspaces",
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="workspaces",
        help_text="Owning organization; derives GeoServer ACL roles from its slug.",
    )
    name = models.CharField(
        max_length=100,
        help_text="Workspace name (e.g., 'mobility', 'environment')",
    )
    description = models.TextField(blank=True, help_text="Description of this workspace")
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
        db_index=True,
        help_text="PRIVATE: owner org only. PUBLIC: anonymous read + owner-org write.",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Workspace"
        verbose_name_plural = "Workspaces"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["geodata_engine", "name"], name="unique_workspace_name_per_engine"
            ),
        ]

    def __str__(self) -> str:
        if self.geodata_engine:
            return f"{self.geodata_engine.name} -> {self.name}"
        return self.name

    def save(self, *args, **kwargs) -> None:
        """Save wrapped in `atomic()` so a failed ACL push (see `signals.py`,
        epic-11 ticket 09) rolls back the row too -- a Workspace must never
        exist in Django without a matching enforced GeoServer ACL."""
        self.full_clean()
        with transaction.atomic():
            super().save(*args, **kwargs)


class Store(SyncStateMixin, TimeStampedModel, EncryptedCharField):
    """
    Generic data store abstraction.

    A store is owned by a :class:`Workspace` (and therefore by an engine).
    PostGIS stores are the only kind that can back a published Layer; file
    and GeoTIFF stores exist for raster / file-based data sources.
    """

    class StoreType(models.TextChoices):
        POSTGIS = "postgis", "PostGIS Database"
        FILE = "file", "File-based Store (Shapefile, GeoPackage, GeoJSON, Directory)"
        GEOTIFF = "geotiff", "GeoTIFF"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    geodata_engine = models.ForeignKey(
        GeodataEngine,
        on_delete=models.CASCADE,
        related_name="stores",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        "Workspace",
        on_delete=models.CASCADE,
        related_name="stores",
        null=True,
        blank=True,
        help_text="Workspace this store belongs to",
    )
    name = models.CharField(max_length=100, help_text="Store name for identification")

    store_type = models.CharField(
        max_length=20,
        choices=StoreType.choices,
        default=StoreType.POSTGIS,
        help_text="Type of data store",
    )

    # PostGIS-specific fields (optional for other store types)
    host = models.CharField(max_length=255, blank=True, help_text="PostGIS host (for PostGIS stores)")
    port = models.IntegerField(default=5432, blank=True, null=True, help_text="PostGIS port (for PostGIS stores)")
    database = models.CharField(max_length=100, blank=True, help_text="PostGIS database name (for PostGIS stores)")
    username = models.CharField(max_length=100, blank=True, help_text="PostGIS username (for PostGIS stores)")
    password = models.CharField(max_length=100, blank=True, help_text="PostGIS password (for PostGIS stores)")
    schema = models.CharField(
        max_length=100,
        default="public",
        blank=True,
        help_text="PostGIS schema (for PostGIS stores)",
    )

    # File-based store fields
    file_path = models.CharField(max_length=500, blank=True, help_text="File or directory path")
    charset = models.CharField(max_length=50, default="UTF-8", blank=True, help_text="Character encoding")

    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Data Store"
        verbose_name_plural = "Data Stores"
        ordering = ["store_type", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], name="unique_store_name_per_workspace"
            ),
        ]

    def __str__(self) -> str:
        if self.geodata_engine:
            return f"{self.geodata_engine.name} -> {self.name}"
        return self.name

    def clean(self) -> None:
        super().clean()
        # Inherit engine from workspace when only the workspace is set; reject
        # explicit mismatches so the admin surfaces a clean field-level error.
        if self.workspace:
            if not self.geodata_engine:
                self.geodata_engine = self.workspace.geodata_engine
            elif self.workspace.geodata_engine_id != self.geodata_engine_id:
                raise ValidationError(
                    {"geodata_engine": "Store geodata engine must match the selected workspace."}
                )
        self._validate_store_config()

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def _validate_store_config(self) -> None:
        """Validate required fields based on store type."""
        errors: dict[str, str] = {}
        if self.store_type == self.StoreType.POSTGIS:
            for field in ("host", "database", "username"):
                if not getattr(self, field):
                    errors[field] = "This field is required for PostGIS stores."
        elif self.store_type in {self.StoreType.FILE, self.StoreType.GEOTIFF} and not self.file_path:
            errors["file_path"] = f"This field is required for {self.store_type} stores."

        if errors:
            raise ValidationError(errors)

    @property
    def decrypted_password(self) -> str:
        """Return the decrypted PostGIS password."""
        return self.decrypt_field("password", self.password)

    def has_usable_password(self) -> bool:
        """Return True if the store has a usable (decryptable) password."""
        try:
            return bool(self.decrypted_password)
        except (ValueError, Exception):
            return False


class LayerQuerySet(models.QuerySet):
    def public(self):
        """Layers visible to an anonymous/unauthenticated reader: is_public only.

        Deliberately does NOT also filter publishing_state — that matches
        LayerViewSet's existing behavior exactly. (catalog_api's
        CatalogVisibilityService applies a stricter, separate rule for its
        own GeoServer-compatible surface; not reused here to avoid a
        behavior change.)
        """
        return self.filter(is_public=True)


class Layer(SyncStateMixin, TimeStampedModel):
    """
    Logical dataset backed by a PostGIS table or view.

    Publishing is explicit and delegated to services; the model only carries
    the minimum metadata needed to describe the layer and track its
    publishing state.
    """

    class GeometryType(models.TextChoices):
        POINT = "Point", "Point"
        LINE_STRING = "LineString", "LineString"
        POLYGON = "Polygon", "Polygon"
        MULTI_POINT = "MultiPoint", "MultiPoint"
        MULTI_LINE_STRING = "MultiLineString", "MultiLineString"
        MULTI_POLYGON = "MultiPolygon", "MultiPolygon"
        GEOMETRY_COLLECTION = "GeometryCollection", "GeometryCollection"

    class PublishingState(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        FAILED = "FAILED", "Failed"
        UNPUBLISHED = "UNPUBLISHED", "Unpublished"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)

    objects = LayerQuerySet.as_manager()

    name = models.CharField(max_length=100, help_text="Layer name")
    title = models.CharField(max_length=200, blank=True, help_text="Human-readable title")
    description = models.TextField(
        blank=True,
        help_text="Generated plain-text projection of the authored rich description.",
    )
    description_content = models.JSONField(
        default=empty_document,
        blank=True,
        help_text="Public rich description authored in TOSCA.",
    )
    provider_description = models.TextField(
        blank=True,
        editable=False,
        help_text="Last description observed from the provider; never overwrites authored content.",
    )

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="layers")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="layers")

    table_name = models.CharField(max_length=100, help_text="PostGIS table name")
    geometry_column = models.CharField(max_length=100, default="geom", help_text="Geometry column name")
    geometry_type = models.CharField(max_length=50, choices=GeometryType.choices, help_text="Geometry type")
    srid = models.IntegerField(default=4326, help_text="Spatial Reference System Identifier")

    publishing_state = models.CharField(
        max_length=20,
        choices=PublishingState.choices,
        default=PublishingState.DRAFT,
        help_text="Current publishing state",
    )
    is_public = models.BooleanField(
        default=False,
        help_text="If true, layer can be listed and retrieved without authentication.",
    )
    queryable = models.BooleanField(
        default=True,
        help_text="GeoServer WMS queryable layer setting.",
    )
    opaque = models.BooleanField(
        default=False,
        help_text="GeoServer WMS opaque layer setting.",
    )
    published_url = models.URLField(blank=True, help_text="Published layer URL (WFS/WMS)")
    publishing_error = models.TextField(blank=True, help_text="Last publishing error message")
    published_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Layer"
        verbose_name_plural = "Layers"
        ordering = ["workspace__name", "name"]
        indexes = [
            # Matches CatalogVisibilityService's Exists subquery and direct
            # catalog listing filter (workspace + is_public + publishing_state).
            models.Index(
                fields=["workspace", "is_public", "publishing_state"],
                name="geoprov_layer_ws_pub_state_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], name="unique_layer_name_per_workspace"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.workspace.name}/{self.name}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        existing_blocks = (
            self.description_content.get("blocks")
            if isinstance(self.description_content, dict)
            else None
        )
        if self._state.adding and not existing_blocks and self.description:
            self.description_content = description_document_from_text(self.description)
        try:
            self.description_content = validate_description_document(self.description_content)
            self.description = description_document_to_text(self.description_content)
        except ValidationError as exc:
            errors["description_content"] = exc.messages
        # Raster (geotiff) and PostGIS stores are both first-class layer
        # backings — see catalog_api.services.v1.geoserver_v1_builder, which
        # branches on store_type to build the right detail shape.
        if self.store_id and self.workspace_id:
            if self.store.workspace_id != self.workspace_id:
                errors["store"] = "Store must belong to the selected workspace."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def full_table_name(self) -> str:
        """Return the fully qualified table name as ``schema.table``."""
        return f"{self.store.schema}.{self.table_name}"

    @property
    def is_published(self) -> bool:
        """Return True if the layer is currently published."""
        return self.publishing_state == self.PublishingState.PUBLISHED

    def usage_summary(self) -> dict[str, int]:
        """
        Return how many GeoStory / Event / GeoFeedback rows reference this
        layer through their respective through-tables.

        Used by the admin delete-confirmation page and the API destroy
        action to warn an operator before a CASCADE removes the layer
        from every parent that depends on it.
        """
        return {
            "geostories": self.geostory_uses.count(),
            "events": self.event_uses.count(),
            "feedbacks": self.feedback_uses.count(),
        }


def sprite_image_upload_to(instance: "SpriteAsset", filename: str) -> str:
    """Use an opaque path and a fixed extension for MapLibre sprite sheets."""
    return f"geodata/sprites/{instance.id}/sprite.png"


def sprite_image_2x_upload_to(instance: "SpriteAsset", filename: str) -> str:
    """Store the optional high-DPI sprite beside its 1x counterpart."""
    return f"geodata/sprites/{instance.id}/sprite@2x.png"


def layer_group_legend_upload_to(instance: "LayerGroup", filename: str) -> str:
    """Store one curated legend image below an opaque group-owned path."""
    extension = Path(filename).suffix.lower() or ".png"
    return f"geodata/layer-groups/{instance.id}/legend{extension}"


class SpriteAsset(TimeStampedModel):
    """A MapLibre sprite sheet and its JSON image index."""

    class ValidationState(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Unknown"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    geodata_engine = models.ForeignKey(
        GeodataEngine,
        on_delete=models.CASCADE,
        related_name="sprite_assets",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="sprite_assets",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to=sprite_image_upload_to)
    index_content = models.JSONField(default=dict)
    image_2x = models.ImageField(upload_to=sprite_image_2x_upload_to, blank=True)
    index_content_2x = models.JSONField(default=dict, blank=True)
    content_hash = models.CharField(max_length=64, editable=False, blank=True)
    validation_state = models.CharField(
        max_length=20,
        choices=ValidationState.choices,
        default=ValidationState.UNKNOWN,
    )
    validation_errors = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        ordering = ["geodata_engine__name", "workspace__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["geodata_engine", "workspace", "name"],
                condition=models.Q(workspace__isnull=False),
                name="unique_sprite_per_engine_workspace_name",
            ),
            models.UniqueConstraint(
                fields=["geodata_engine", "name"],
                condition=models.Q(workspace__isnull=True),
                name="unique_global_sprite_per_engine_name",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.workspace.name}:{self.name}" if self.workspace else self.name

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.workspace and self.workspace.geodata_engine_id != self.geodata_engine_id:
            errors["workspace"] = "Sprite workspace must belong to the selected geodata engine."

        index = self.index_content
        if not isinstance(index, dict) or not index:
            errors["index_content"] = "Sprite index must be a non-empty JSON object."

        image_width, image_height, image_error = self._inspect_image(self.image)
        if image_error:
            errors["image"] = image_error

        if isinstance(index, dict):
            index_errors = self._validate_index(
                index,
                image_width,
                image_height,
                expected_pixel_ratio=1,
            )
            if index_errors:
                errors["index_content"] = " | ".join(index_errors)

        has_image_2x = bool(self.image_2x)
        has_index_2x = isinstance(self.index_content_2x, dict) and bool(self.index_content_2x)
        if has_image_2x != has_index_2x:
            message = "Upload both the @2x PNG and its @2x JSON index, or leave both empty."
            if not has_image_2x:
                errors["image_2x"] = message
            if not has_index_2x:
                errors["index_content_2x"] = message

        if has_image_2x and has_index_2x:
            image_width_2x, image_height_2x, image_error_2x = self._inspect_image(
                self.image_2x
            )
            if image_error_2x:
                errors["image_2x"] = image_error_2x

            index_errors_2x = self._validate_index(
                self.index_content_2x,
                image_width_2x,
                image_height_2x,
                expected_pixel_ratio=2,
            )
            if index_errors_2x:
                errors["index_content_2x"] = " | ".join(index_errors_2x)

            pair_errors = self._validate_resolution_pair(index, self.index_content_2x)
            if pair_errors:
                existing = errors.get("index_content_2x")
                errors["index_content_2x"] = " | ".join(
                    ([existing] if existing else []) + pair_errors
                )

        self.validation_state = (
            self.ValidationState.INVALID if errors else self.ValidationState.VALID
        )
        self.validation_errors = list(errors.values())
        if errors:
            raise ValidationError(errors)

    @staticmethod
    def _inspect_image(image_field) -> tuple[int | None, int | None, str | None]:
        if not image_field:
            return None, None, None
        try:
            image_field.open("rb")
            with Image.open(image_field.file) as sprite_image:
                if sprite_image.format != "PNG":
                    return None, None, "Sprite image must be a PNG file."
                width, height = sprite_image.size
            image_field.file.seek(0)
            return width, height, None
        except (OSError, ValueError, UnidentifiedImageError):
            return None, None, "Sprite image is not a readable PNG file."

    @staticmethod
    def _validate_index(
        index: dict,
        image_width: int | None,
        image_height: int | None,
        *,
        expected_pixel_ratio: int,
    ) -> list[str]:
        errors: list[str] = []
        required = ("width", "height", "x", "y", "pixelRatio")
        for key, entry in index.items():
            if not isinstance(key, str) or not key:
                errors.append("Sprite names must be non-empty strings.")
                continue
            if ":" in key:
                errors.append(f"Sprite name '{key}' cannot contain ':'.")
                continue
            if not isinstance(entry, dict):
                errors.append(f"Sprite '{key}' metadata must be an object.")
                continue
            missing = [field for field in required if field not in entry]
            if missing:
                errors.append(f"Sprite '{key}' is missing: {', '.join(missing)}.")
                continue
            values = [entry[field] for field in required]
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
                errors.append(f"Sprite '{key}' dimensions must be numeric.")
                continue
            if entry["width"] <= 0 or entry["height"] <= 0 or entry["pixelRatio"] <= 0:
                errors.append(f"Sprite '{key}' dimensions and pixelRatio must be positive.")
            if entry["pixelRatio"] != expected_pixel_ratio:
                errors.append(
                    f"Sprite '{key}' pixelRatio must be {expected_pixel_ratio} "
                    f"for the {expected_pixel_ratio}x index."
                )
            if entry["x"] < 0 or entry["y"] < 0:
                errors.append(f"Sprite '{key}' coordinates cannot be negative.")
            if image_width is not None and image_height is not None:
                if entry["x"] + entry["width"] > image_width or entry["y"] + entry["height"] > image_height:
                    errors.append(f"Sprite '{key}' lies outside the PNG bounds.")
        return errors

    @staticmethod
    def _validate_resolution_pair(index: dict, index_2x: dict) -> list[str]:
        if not isinstance(index, dict) or not isinstance(index_2x, dict):
            return []

        errors: list[str] = []
        names = set(index)
        names_2x = set(index_2x)
        if names != names_2x:
            missing = sorted(names - names_2x)
            extra = sorted(names_2x - names)
            if missing:
                errors.append("@2x index is missing sprites: " + ", ".join(missing) + ".")
            if extra:
                errors.append("@2x index has additional sprites: " + ", ".join(extra) + ".")

        for name in sorted(names & names_2x):
            entry = index[name]
            entry_2x = index_2x[name]
            if not isinstance(entry, dict) or not isinstance(entry_2x, dict):
                continue
            required = {"width", "height", "pixelRatio"}
            if not required.issubset(entry) or not required.issubset(entry_2x):
                continue
            values = [
                entry["width"],
                entry["height"],
                entry["pixelRatio"],
                entry_2x["width"],
                entry_2x["height"],
                entry_2x["pixelRatio"],
            ]
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in values
            ):
                continue
            if entry["pixelRatio"] <= 0 or entry_2x["pixelRatio"] <= 0:
                continue
            logical_size = (
                entry["width"] / entry["pixelRatio"],
                entry["height"] / entry["pixelRatio"],
            )
            logical_size_2x = (
                entry_2x["width"] / entry_2x["pixelRatio"],
                entry_2x["height"] / entry_2x["pixelRatio"],
            )
            if logical_size != logical_size_2x:
                errors.append(
                    f"Sprite '{name}' must have the same logical dimensions in "
                    "the 1x and @2x indexes."
                )
        return errors

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        digest = hashlib.sha256()
        for label, index_content, image_field in (
            ("1x", self.index_content, self.image),
            ("2x", self.index_content_2x, self.image_2x),
        ):
            digest.update(label.encode("ascii"))
            digest.update(
                json.dumps(index_content, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if image_field:
                image_field.open("rb")
                for chunk in iter(lambda: image_field.file.read(64 * 1024), b""):
                    digest.update(chunk)
                image_field.file.seek(0)
        self.content_hash = digest.hexdigest()
        super().save(*args, **kwargs)


class Style(SyncStateMixin, TimeStampedModel):
    """
    Provider-owned style definition backed by GeoServer SLD or MBStyle content.

    Styles are global (engine-scoped) by default and may optionally be
    workspace-scoped. The active validation and remote upload state is
    tracked on the row so the admin and sync services can reason about
    whether the style is safe to assign to a layer.
    """

    class StyleFormat(models.TextChoices):
        SLD = "sld", "SLD"
        MBSTYLE = "mbstyle", "MBStyle"

    class ValidationState(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Unknown"
        VALID = "VALID", "Valid"
        INVALID = "INVALID", "Invalid"

    class RemoteState(models.TextChoices):
        LOCAL_ONLY = "LOCAL_ONLY", "Local only"
        SYNCED = "SYNCED", "Synced"
        FAILED = "FAILED", "Failed"
        UNSUPPORTED = "UNSUPPORTED", "Unsupported by provider"
        DELETED = "DELETED", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    geodata_engine = models.ForeignKey(
        GeodataEngine,
        on_delete=models.CASCADE,
        related_name="styles",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="styles",
        null=True,
        blank=True,
        help_text="Workspace-scoped style. Leave blank for a global engine style.",
    )
    name = models.CharField(max_length=100, help_text="GeoServer style identifier")
    title = models.CharField(max_length=200, blank=True, help_text="Human-readable title")
    description = models.TextField(blank=True)
    format = models.CharField(max_length=20, choices=StyleFormat.choices)
    file_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Original uploaded file name, when Django has local style content.",
    )
    file_content = models.TextField(
        blank=True,
        help_text="Raw SLD XML or MBStyle JSON content, when available.",
    )
    sprite_asset = models.ForeignKey(
        SpriteAsset,
        on_delete=models.PROTECT,
        related_name="styles",
        null=True,
        blank=True,
        help_text="Optional MapLibre sprite sheet used by this MBStyle.",
    )
    content_hash = models.CharField(max_length=64, editable=False)
    validation_state = models.CharField(
        max_length=20,
        choices=ValidationState.choices,
        default=ValidationState.UNKNOWN,
    )
    validation_errors = models.JSONField(default=list, blank=True)
    remote_state = models.CharField(
        max_length=20,
        choices=RemoteState.choices,
        default=RemoteState.LOCAL_ONLY,
    )
    remote_error = models.TextField(blank=True)
    remote_uploaded_at = models.DateTimeField(null=True, blank=True)
    remote_verified_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Style"
        verbose_name_plural = "Styles"
        ordering = ["geodata_engine__name", "workspace__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["geodata_engine", "workspace", "name"],
                condition=models.Q(workspace__isnull=False),
                name="unique_style_per_engine_workspace_name",
            ),
            models.UniqueConstraint(
                fields=["geodata_engine", "name"],
                condition=models.Q(workspace__isnull=True),
                name="unique_global_style_per_engine_name",
            ),
        ]

    def __str__(self) -> str:
        return self.qualified_name

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.workspace and self.workspace.geodata_engine_id != self.geodata_engine_id:
            errors["workspace"] = "Style workspace must belong to the selected geodata engine."
        if self.validation_errors is None:
            self.validation_errors = []
        if self.sprite_asset_id:
            if self.format != self.StyleFormat.MBSTYLE:
                errors["sprite_asset"] = "Sprites can only be attached to MBStyle styles."
            elif self.sprite_asset.geodata_engine_id != self.geodata_engine_id:
                errors["sprite_asset"] = "Sprite and style must use the same geodata engine."
            elif self.workspace_id and self.sprite_asset.workspace_id not in (None, self.workspace_id):
                errors["sprite_asset"] = "Sprite must be global or belong to the style workspace."

        if self.format == self.StyleFormat.MBSTYLE:
            requires_sprite, static_references = self._mbstyle_sprite_references()
            if requires_sprite and not self.sprite_asset_id:
                errors["sprite_asset"] = "This MBStyle uses icons or patterns and requires a sprite asset."
            elif self.sprite_asset_id:
                missing = sorted(static_references - set(self.sprite_asset.index_content))
                if missing:
                    errors["sprite_asset"] = (
                        "Sprite asset is missing MBStyle images: " + ", ".join(missing) + "."
                    )

        if errors:
            raise ValidationError(errors)

    def _mbstyle_sprite_references(self) -> tuple[bool, set[str]]:
        """Return whether an MBStyle uses sprites and its statically named images."""
        try:
            payload = json.loads(self.file_content)
        except (TypeError, json.JSONDecodeError):
            return False, set()
        if not isinstance(payload, dict) or not isinstance(payload.get("layers"), list):
            return False, set()

        requires_sprite = False
        references: set[str] = set()
        for layer in payload["layers"]:
            if not isinstance(layer, dict):
                continue
            for section, properties in (
                ("layout", ("icon-image",)),
                (
                    "paint",
                    (
                        "background-pattern",
                        "fill-pattern",
                        "fill-extrusion-pattern",
                        "line-pattern",
                    ),
                ),
            ):
                values = layer.get(section)
                if not isinstance(values, dict):
                    continue
                for property_name in properties:
                    value = values.get(property_name)
                    if value is None:
                        continue
                    requires_sprite = True
                    if isinstance(value, str) and value and "{" not in value:
                        references.add(value)
        return requires_sprite, references

    def save(self, *args, **kwargs) -> None:
        self.content_hash = hashlib.sha256((self.file_content or "").encode("utf-8")).hexdigest()
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_global(self) -> bool:
        return self.workspace_id is None

    @property
    def is_valid(self) -> bool:
        return self.validation_state == self.ValidationState.VALID

    @property
    def is_synced(self) -> bool:
        return self.remote_state == self.RemoteState.SYNCED

    @property
    def is_remote_supported(self) -> bool:
        return self.remote_state != self.RemoteState.UNSUPPORTED

    @property
    def qualified_name(self) -> str:
        if self.workspace:
            return f"{self.workspace.name}:{self.name}"
        return self.name


class LayerStyleAssignment(TimeStampedModel):
    """
    Technical assignment of a :class:`Style` to a :class:`Layer`.

    A layer may carry one active default style and any number of alternate
    styles. The "single active default" invariant is enforced both at the
    DB level (partial unique constraint) and in :meth:`clean` so the admin
    surfaces a form-friendly error before the DB constraint fires.
    """

    class Role(models.TextChoices):
        DEFAULT = "default", "Default"
        ALTERNATE = "alternate", "Alternate"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    layer = models.ForeignKey(
        Layer,
        on_delete=models.CASCADE,
        related_name="style_assignments",
    )
    style = models.ForeignKey(
        Style,
        on_delete=models.CASCADE,
        related_name="layer_assignments",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.DEFAULT)
    is_active = models.BooleanField(default=True)
    style_layer_ids = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Ordered MBStyle layer IDs that apply to this data layer. "
            "Leave empty for SLD assignments."
        ),
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        db_table = "geodata_providers_layerstyle"
        verbose_name = "Layer Style Assignment"
        verbose_name_plural = "Layer Style Assignments"
        ordering = ["layer__workspace__name", "layer__name", "role", "style__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["layer"],
                condition=models.Q(role="default", is_active=True),
                name="unique_active_default_style_per_layer",
            ),
            models.UniqueConstraint(
                fields=["layer", "style", "role"],
                name="unique_layer_style_role",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.layer} -> {self.style} ({self.role})"

    def clean(self) -> None:
        super().clean()
        if not self.layer_id or not self.style_id:
            return

        errors: dict[str, str] = {}
        if self.style.validation_state == Style.ValidationState.INVALID:
            errors["style"] = "Invalid styles cannot be assigned to layers."
        selected_ids = self.style_layer_ids
        if self.style.format == Style.StyleFormat.MBSTYLE and selected_ids == []:
            selected_ids = self.inferred_mbstyle_layer_ids()
            if selected_ids:
                # An active MBStyle assignment with no selected rules cannot be
                # rendered or published. Infer the rules at the persistence
                # boundary so admin, scripts, and API callers behave alike.
                self.style_layer_ids = selected_ids
        if not isinstance(selected_ids, list) or any(
            not isinstance(layer_id, str) or not layer_id.strip()
            for layer_id in selected_ids
        ):
            errors["style_layer_ids"] = "Style layer IDs must be a list of non-empty strings."
        elif len(selected_ids) != len(set(selected_ids)):
            errors["style_layer_ids"] = "Style layer IDs cannot contain duplicates."
        elif self.style.format == Style.StyleFormat.SLD and selected_ids:
            errors["style_layer_ids"] = "SLD assignments cannot select MBStyle layer IDs."
        elif self.style.format == Style.StyleFormat.MBSTYLE and selected_ids:
            available_ids = {
                style_layer.get("id")
                for style_layer in self._mbstyle_layers()
                if isinstance(style_layer.get("id"), str)
            }
            missing_ids = [layer_id for layer_id in selected_ids if layer_id not in available_ids]
            if missing_ids:
                errors["style_layer_ids"] = (
                    "Unknown MBStyle layer IDs: " + ", ".join(missing_ids) + "."
                )
        # Application-level mirror of unique_active_default_style_per_layer:
        # surfaces a clean ValidationError before the DB IntegrityError fires.
        if (
            self.role == self.Role.DEFAULT
            and self.is_active
            and not getattr(self, "_defer_active_default_validation", False)
        ):
            existing_default = LayerStyleAssignment.objects.filter(
                layer=self.layer,
                role=self.Role.DEFAULT,
                is_active=True,
            )
            if self.pk:
                existing_default = existing_default.exclude(pk=self.pk)
            if existing_default.exists():
                errors["role"] = "Only one active default style is allowed per layer."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def _mbstyle_layers(self) -> list[dict]:
        try:
            payload = json.loads(self.style.file_content)
        except (TypeError, json.JSONDecodeError):
            return []
        layers = payload.get("layers", []) if isinstance(payload, dict) else []
        return [layer for layer in layers if isinstance(layer, dict)]

    def inferred_mbstyle_layer_ids(self) -> list[str]:
        """Infer this data layer's render rules from an MBStyle document.

        Prefer rules whose ``source`` or ``source-layer`` identifies the
        assigned layer. A style containing only one logical vector source is
        also unambiguous, so all of its vector-renderable rules are selected.
        Multi-source styles without a matching source remain explicit and must
        be pinned by the author.
        """
        if not self.layer_id or not self.style_id or self.style.format != Style.StyleFormat.MBSTYLE:
            return []

        vector_layer_types = {"fill", "line", "symbol", "circle", "heatmap", "fill-extrusion"}
        style_layers = [
            style_layer
            for style_layer in self._mbstyle_layers()
            if style_layer.get("type") in vector_layer_types
            and isinstance(style_layer.get("id"), str)
            and style_layer["id"].strip()
        ]
        if not style_layers:
            return []

        target_names = {
            self.layer.name,
            f"{self.layer.workspace.name}:{self.layer.name}",
            f"{self.layer.workspace.name}/{self.layer.name}",
        }

        def references(style_layer: dict) -> set[str]:
            return {
                value
                for key in ("source-layer", "source")
                if isinstance((value := style_layer.get(key)), str) and value
            }

        matching_ids = [
            style_layer["id"]
            for style_layer in style_layers
            if references(style_layer) & target_names
            or any(
                reference.rsplit(":", 1)[-1].rsplit("/", 1)[-1] == self.layer.name
                for reference in references(style_layer)
            )
        ]
        if matching_ids:
            return matching_ids

        logical_sources = {
            (style_layer.get("source"), style_layer.get("source-layer"))
            for style_layer in style_layers
        }
        if len(logical_sources) <= 1:
            return [style_layer["id"] for style_layer in style_layers]
        return []

    def selected_mbstyle_layers(self, layer_ids: list[str] | None = None) -> list[dict]:
        """Return selected MBStyle rules in document order."""
        selected_ids = set(self.style_layer_ids if layer_ids is None else layer_ids)
        return [
            layer
            for layer in self._mbstyle_layers()
            if layer.get("id") in selected_ids
        ]


class LayerGroup(SyncStateMixin, TimeStampedModel):
    """A public catalog item that renders an ordered set of existing layers."""

    class PublishingState(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        FAILED = "FAILED", "Failed"
        UNPUBLISHED = "UNPUBLISHED", "Unpublished"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="layer_groups",
    )
    name = models.CharField(max_length=100)
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(
        blank=True,
        help_text="Generated plain-text projection of the authored rich description.",
    )
    description_content = models.JSONField(
        default=empty_document,
        blank=True,
        help_text="Public rich description authored in TOSCA.",
    )
    legend_image = models.ImageField(
        upload_to=layer_group_legend_upload_to,
        blank=True,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp"])],
        help_text=(
            "Optional curated legend for the complete group. PNG, JPEG, and WebP are "
            "supported; when present it replaces generated member legends."
        ),
    )
    legend_content_hash = models.CharField(max_length=64, editable=False, blank=True)
    legend_composition_hash = models.CharField(max_length=64, editable=False, blank=True)
    publishing_state = models.CharField(
        max_length=20,
        choices=PublishingState.choices,
        default=PublishingState.DRAFT,
    )
    is_public = models.BooleanField(default=False)
    publishing_error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        ordering = ["workspace__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                name="unique_layer_group_name_per_workspace",
            ),
        ]
        indexes = [
            models.Index(
                fields=["workspace", "is_public", "publishing_state"],
                name="geoprov_group_ws_pub_state_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.workspace.name}/{self.name}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, object] = {}
        if self.workspace_id and self.workspace.geodata_engine_id is None:
            errors["workspace"] = "Group workspace requires a geodata engine."
        existing_blocks = (
            self.description_content.get("blocks")
            if isinstance(self.description_content, dict)
            else None
        )
        if self._state.adding and not existing_blocks and self.description:
            self.description_content = description_document_from_text(self.description)
        try:
            self.description_content = validate_description_document(self.description_content)
            self.description = description_document_to_text(self.description_content)
        except ValidationError as exc:
            errors["description_content"] = exc.messages
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        legend_uploaded = bool(
            self.legend_image and not getattr(self.legend_image, "_committed", True)
        )
        self.full_clean()
        if legend_uploaded:
            self.legend_content_hash = self._hash_legend_image()
            self.legend_composition_hash = (
                self.current_legend_composition_hash() if self.pk else ""
            )
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "legend_content_hash",
                    "legend_composition_hash",
                }
        elif not self.legend_image:
            self.legend_content_hash = ""
            self.legend_composition_hash = ""
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "legend_content_hash",
                    "legend_composition_hash",
                }
        super().save(*args, **kwargs)

    def _hash_legend_image(self) -> str:
        digest = hashlib.sha256()
        self.legend_image.open("rb")
        for chunk in iter(lambda: self.legend_image.file.read(64 * 1024), b""):
            digest.update(chunk)
        self.legend_image.file.seek(0)
        return digest.hexdigest()

    def current_legend_composition_hash(self) -> str:
        """Fingerprint every rendered or labelled input represented by a legend."""
        if not self.pk:
            return ""
        # Reuse a prefetched ``members`` cache (the visible-group list prefetches
        # ``layer`` / ``style_assignment__style__sprite_asset``) to keep the
        # listing a single query; fall back to an explicit join off the save
        # path, where no prefetch exists.
        if "members" in getattr(self, "_prefetched_objects_cache", {}):
            members = self.members.all()
        else:
            members = self.members.select_related(
                "layer",
                "style_assignment__style__sprite_asset",
            ).order_by("order", "id")
        payload = []
        for member in members:
            assignment = member.style_assignment
            style = None if assignment is None else assignment.style
            sprite = None if style is None else style.sprite_asset
            payload.append({
                "id": str(member.id),
                "layer_id": str(member.layer_id),
                "layer_name": member.layer.name,
                "layer_title": member.layer.title,
                "member_title": member.title,
                "source_alias": member.source_alias,
                "order": member.order,
                "assignment_id": None if assignment is None else str(assignment.id),
                "style_id": None if style is None else str(style.id),
                "style_hash": None if style is None else style.content_hash,
                "style_layer_ids": [] if assignment is None else assignment.style_layer_ids,
                "render_layer_ids": member.render_layer_ids,
                "sprite_hash": None if sprite is None else sprite.content_hash,
            })
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @property
    def legend_is_stale(self) -> bool:
        if not self.legend_image:
            return False
        return (
            not self.legend_composition_hash
            or self.legend_composition_hash != self.current_legend_composition_hash()
        )

    def refresh_legend_composition_hash(self) -> None:
        """Mark the uploaded legend as representing the current saved composition."""
        next_hash = self.current_legend_composition_hash() if self.legend_image else ""
        type(self).objects.filter(pk=self.pk).update(legend_composition_hash=next_hash)
        self.legend_composition_hash = next_hash

    def validate_members(self) -> None:
        members = list(
            self.members.select_related(
                "layer__store",
                "style_assignment__style__sprite_asset",
            ).order_by("order", "id")
        )
        errors: list[str] = []
        if len(members) < 2:
            errors.append("A layer group must contain at least two layers.")
        for member in members:
            is_raster = member.layer.store.store_type == Store.StoreType.GEOTIFF
            if self.publishing_state == self.PublishingState.PUBLISHED:
                if not member.layer.is_public or not member.layer.is_published:
                    errors.append(f"Layer '{member.layer.name}' must be public and published.")
                if member.layer.sync_state in {self.SyncState.FAILED, self.SyncState.STALE}:
                    errors.append(f"Layer '{member.layer.name}' is not synchronized.")
            assignment = member.style_assignment
            if assignment is None:
                if self.publishing_state == self.PublishingState.PUBLISHED:
                    errors.append(f"Layer '{member.layer.name}' requires a style assignment.")
                continue
            if assignment.layer_id != member.layer_id:
                errors.append(
                    f"Style assignment for '{member.layer.name}' belongs to another layer."
                )
                continue
            style = assignment.style
            if not assignment.is_active:
                errors.append(f"Style assignment for '{member.layer.name}' is inactive.")
            if style.validation_state != Style.ValidationState.VALID:
                errors.append(f"Style for '{member.layer.name}' is not valid.")
            if style.geodata_engine_id != self.workspace.geodata_engine_id:
                errors.append(f"Style for '{member.layer.name}' uses another geodata engine.")
            if style.workspace_id not in (None, self.workspace_id):
                errors.append(f"Style for '{member.layer.name}' belongs to another workspace.")
            expected_format = Style.StyleFormat.SLD if is_raster else Style.StyleFormat.MBSTYLE
            if style.format != expected_format:
                data_type = "raster" if is_raster else "vector"
                errors.append(
                    f"Layer '{member.layer.name}' is {data_type} and requires an "
                    f"{expected_format.upper()} style."
                )
            elif not is_raster:
                self._validate_mbstyle_assignment(member, errors)
        if errors:
            raise ValidationError({"members": errors})

    @staticmethod
    def _validate_mbstyle_assignment(member: "LayerGroupMember", errors: list[str]) -> None:
        assignment = member.style_assignment
        effective_layer_ids = member.effective_style_layer_ids
        selected_layers = assignment.selected_mbstyle_layers(effective_layer_ids)
        if not effective_layer_ids:
            errors.append(
                f"Style assignment for '{member.layer.name}' must select at least one MBStyle layer."
            )
            return
        if len(selected_layers) != len(effective_layer_ids):
            errors.append(f"Style assignment for '{member.layer.name}' has missing MBStyle layers.")
            return
        for style_layer in selected_layers:
            layer_id = style_layer.get("id")
            if style_layer.get("type") in {"background", "raster", "hillshade"}:
                errors.append(
                    f"MBStyle layer '{layer_id}' cannot render the vector layer "
                    f"'{member.layer.name}'."
                )

    @property
    def composition(self) -> str:
        member_types = {
            "RASTER" if member.layer.store.store_type == Store.StoreType.GEOTIFF else "VECTOR"
            for member in self.members.all()
        }
        if len(member_types) == 1:
            return next(iter(member_types))
        return "MIXED"

    def publication_warnings(self) -> list[str]:
        """Return non-blocking warnings for visually risky member ordering."""
        # ``members.all()`` reuses a ``prefetch_related("members__layer__store")``
        # cache when present (the visible-group list relies on this to stay a
        # single query) and is already ordered by ``Meta.ordering``.
        return self.publication_warnings_for_layers(
            [(member.layer, member.order) for member in self.members.all()]
        )

    @staticmethod
    def publication_warnings_for_layers(layer_orders) -> list[str]:
        """Return ordering warnings for persisted or not-yet-saved members."""
        members = sorted(layer_orders, key=lambda item: item[1])
        warnings: list[str] = []
        for index, (layer, order) in enumerate(members):
            if index == 0 or layer.store.store_type != Store.StoreType.GEOTIFF:
                continue
            warnings.append(
                f"Raster layer '{layer.title or layer.name}' (order {order}) is above "
                f"{index} lower-order member(s) and may obscure "
                "them. Order 0 is the bottom; the highest order is the top."
            )
        return warnings


class LayerGroupMember(TimeStampedModel):
    """Ordered membership and stable author-facing source alias for a group."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    group = models.ForeignKey(LayerGroup, on_delete=models.CASCADE, related_name="members")
    layer = models.ForeignKey(Layer, on_delete=models.PROTECT, related_name="group_memberships")
    style_assignment = models.ForeignKey(
        LayerStyleAssignment,
        on_delete=models.PROTECT,
        related_name="group_memberships",
        null=True,
        blank=True,
        help_text="Pinned style assignment; defaults to the layer's active default assignment.",
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional label for this render pass; defaults to the layer title.",
    )
    render_layer_ids = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Optional MBStyle layer IDs for this group member. "
            "Leave empty to use the pinned assignment's selected layer IDs."
        ),
    )
    order = models.PositiveIntegerField(default=0)
    source_alias = models.CharField(
        max_length=100,
        blank=True,
        help_text="Source name referenced by MBStyle layers; defaults to the layer name.",
    )

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["group", "order"], name="unique_order_per_group"),
            models.UniqueConstraint(fields=["group", "source_alias"], name="unique_source_alias_per_group"),
        ]

    def __str__(self) -> str:
        return f"{self.group} -> {self.layer}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.group_id and self.layer_id and self.layer.workspace_id != self.group.workspace_id:
            errors["layer"] = "Group members must belong to the group workspace."
        if self.layer_id and self.style_assignment_id is None:
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
        if not isinstance(self.render_layer_ids, list) or any(
            not isinstance(layer_id, str) or not layer_id.strip()
            for layer_id in self.render_layer_ids
        ):
            errors["render_layer_ids"] = "Render layer IDs must be a list of non-empty strings."
        elif len(self.render_layer_ids) != len(set(self.render_layer_ids)):
            errors["render_layer_ids"] = "Render layer IDs cannot contain duplicates."
        elif (
            self.render_layer_ids
            and self.style_assignment_id
            and self.style_assignment.style.format != Style.StyleFormat.MBSTYLE
        ):
            errors["render_layer_ids"] = "Only MBStyle assignments can select render layer IDs."
        if not self.source_alias and self.layer_id and self.group_id:
            self.source_alias = self._next_source_alias()
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        if self._state.adding and self.group_id and self.order == 0:
            max_order = self.group.members.aggregate(models.Max("order"))["order__max"]
            if max_order is not None:
                self.order = max_order + 1
        if not self.source_alias and self.layer_id and self.group_id:
            self.source_alias = self._next_source_alias()
        self.full_clean()
        super().save(*args, **kwargs)

    def _next_source_alias(self) -> str:
        """Return a stable, unique manifest source key derived from the layer name."""
        base_alias = self.layer.name[:100]
        existing_aliases = set(
            self.group.members.exclude(pk=self.pk).values_list("source_alias", flat=True)
        )
        if base_alias not in existing_aliases:
            return base_alias
        suffix = 2
        while True:
            suffix_text = f"-{suffix}"
            candidate = f"{base_alias[:100 - len(suffix_text)]}{suffix_text}"
            if candidate not in existing_aliases:
                return candidate
            suffix += 1

    @property
    def effective_style_layer_ids(self) -> list[str]:
        """Resolve a group-specific rule selection over the assignment default."""
        if self.render_layer_ids:
            return self.render_layer_ids
        if self.style_assignment_id:
            return self.style_assignment.style_layer_ids
        return []

    @property
    def display_title(self) -> str:
        return self.title or self.layer.title or self.layer.name
