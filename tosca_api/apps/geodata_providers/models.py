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
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
        # default doesn't accidentally clear its own flag.
        if self.is_default:
            GeodataEngine.objects.exclude(pk=self.pk).filter(is_default=True).update(
                is_default=False
            )
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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    geodata_engine = models.ForeignKey(
        GeodataEngine,
        on_delete=models.CASCADE,
        related_name="workspaces",
        null=True,
        blank=True,
    )
    name = models.CharField(
        max_length=100,
        help_text="Workspace name (e.g., 'mobility', 'environment')",
    )
    description = models.TextField(blank=True, help_text="Description of this workspace")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Workspace"
        verbose_name_plural = "Workspaces"
        ordering = ["name"]
        unique_together = [["geodata_engine", "name"]]

    def __str__(self) -> str:
        if self.geodata_engine:
            return f"{self.geodata_engine.name} -> {self.name}"
        return self.name

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
        unique_together = [["workspace", "name"]]

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="Layer name")
    title = models.CharField(max_length=200, blank=True, help_text="Human-readable title")
    description = models.TextField(blank=True)

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
        unique_together = ["workspace", "name"]

    def __str__(self) -> str:
        return f"{self.workspace.name}/{self.name}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
        if self.workspace and self.workspace.geodata_engine_id != self.geodata_engine_id:
            raise ValidationError(
                {"workspace": "Style workspace must belong to the selected geodata engine."}
            )
        if self.validation_errors is None:
            self.validation_errors = []

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
        # Application-level mirror of unique_active_default_style_per_layer:
        # surfaces a clean ValidationError before the DB IntegrityError fires.
        if self.role == self.Role.DEFAULT and self.is_active:
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
