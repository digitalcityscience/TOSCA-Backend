import hashlib
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from .encryption import EncryptedCharField


STYLE_FORMATS = [
    ("sld", "SLD"),
    ("mbstyle", "MBStyle"),
]

VALIDATION_STATES = [
    ("UNKNOWN", "Unknown"),
    ("VALID", "Valid"),
    ("INVALID", "Invalid"),
]

REMOTE_STATES = [
    ("LOCAL_ONLY", "Local only"),
    ("SYNCED", "Synced"),
    ("FAILED", "Failed"),
    ("UNSUPPORTED", "Unsupported by provider"),
    ("DELETED", "Deleted"),
]

LAYER_STYLE_ROLES = [
    ("default", "Default"),
    ("alternate", "Alternate"),
]


class GeodataEngine(models.Model, EncryptedCharField):
    """
    Multi-engine geodata engine definition.
    Supports GeoServer, Martin, pg_tileserv, and future engines.
    """

    ENGINE_TYPES = [
        ('geoserver', 'GeoServer'),
        ('martin', 'Martin Tiles'),
        ('pg_tileserv', 'PostGIS TileServer')
    ]

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
        choices=ENGINE_TYPES,
        default='geoserver',
        help_text="Type of geodata engine",
    )
    base_url = models.CharField(
        max_length=255,
        help_text="Full URL to the engine",
    )
    admin_username = models.CharField(
        max_length=100,
        default='admin2',
        blank=True,
        help_text="Admin username (if applicable)",
    )
    admin_password = models.CharField(
        max_length=100,
        default='geoserver2',
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
    is_active = models.BooleanField(default=True, help_text="Is this engine instance active?")
    is_default = models.BooleanField(default=False, help_text="Is this the default engine instance?")

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        app_label = 'geodata_providers'
        verbose_name = "Geodata Provider"
        verbose_name_plural = "Geodata Providers"
        ordering = ['-is_default', 'name']

    def __str__(self):
        default_marker = " (Default)" if self.is_default else ""
        return f"{self.name}{default_marker}"

    def save(self, *args, **kwargs):
        # Keep a single default engine.
        if self.is_default:
            GeodataEngine.objects.filter(is_default=True).update(is_default=False)
        super().save(*args, **kwargs)

    @property
    def decrypted_admin_password(self):
        """Get decrypted admin password."""
        return self.decrypt_field('admin_password', self.admin_password)

    @property
    def engine_url(self):
        """Generic alias for the engine URL."""
        return self.base_url

    @property
    def geoserver_url(self):
        """Backward-compatible alias for existing GeoServer-centric code."""
        return self.base_url

    def get_client(self):
        """Return engine client instance from the client factory."""
        from .engine_factory import EngineClientFactory

        return EngineClientFactory.create_client(self)


class Workspace(models.Model):
    """
    Logical grouping of data (e.g. 'mobility', 'environment')
    Belongs to a specific GeodataEngine
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    geodata_engine = models.ForeignKey(
        GeodataEngine,
        on_delete=models.CASCADE,
        related_name='workspaces',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100, help_text="Workspace name (e.g., 'mobility', 'environment')")
    description = models.TextField(blank=True, help_text="Description of this workspace")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        app_label = 'geodata_providers'
        verbose_name = "Workspace"
        verbose_name_plural = "Workspaces"
        ordering = ['name']
        unique_together = [['geodata_engine', 'name']]

    def __str__(self):
        if self.geodata_engine:
            return f"{self.geodata_engine.name} -> {self.name}"
        return self.name


class Store(models.Model, EncryptedCharField):
    """
    Represents a generic data store abstraction.
    Belongs to a specific GeodataEngine.
    """

    STORE_TYPES = [
        ('postgis', 'PostGIS Database'),
        ('file', 'File-based Store (Shapefile, GeoPackage, GeoJSON, Directory)'),
        ('geotiff', 'GeoTIFF'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    geodata_engine = models.ForeignKey(
        GeodataEngine,
        on_delete=models.CASCADE,
        related_name='stores',
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        'Workspace',
        on_delete=models.CASCADE,
        related_name='stores',
        null=True,
        blank=True,
        help_text="Workspace this store belongs to",
    )
    name = models.CharField(max_length=100, help_text="Store name for identification")

    store_type = models.CharField(
        max_length=20,
        choices=STORE_TYPES,
        default='postgis',
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
    charset = models.CharField(max_length=50, default='UTF-8', blank=True, help_text="Character encoding")

    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        app_label = 'geodata_providers'
        verbose_name = "Data Store"
        verbose_name_plural = "Data Stores"
        ordering = ['store_type', 'name']
        unique_together = [['workspace', 'name']]

    def __str__(self):
        if self.geodata_engine:
            return f"{self.geodata_engine.name} -> {self.name}"
        return self.name

    def save(self, *args, **kwargs):
        self._validate_store_config()
        super().save(*args, **kwargs)

    def _validate_store_config(self):
        """Validate required fields based on store type."""
        if self.store_type == 'postgis':
            required_fields = ['host', 'database', 'username']
            for field in required_fields:
                if not getattr(self, field):
                    raise ValidationError(f"{field} is required for PostGIS stores")
        elif self.store_type in ['file', 'geotiff'] and not self.file_path:
            raise ValidationError(f"file_path is required for {self.store_type} stores")

    @property
    def decrypted_password(self):
        """Get decrypted password."""
        return self.decrypt_field('password', self.password)

    def has_usable_password(self):
        """Check if the store has a usable (decryptable) password."""
        try:
            return bool(self.decrypted_password)
        except (ValueError, Exception):
            return False


class Layer(models.Model):
    """
    Logical dataset backed by a PostGIS table or view.
    Publishing is explicit and delegated to services.
    """

    GEOMETRY_TYPES = [
        ('Point', 'Point'),
        ('LineString', 'LineString'),
        ('Polygon', 'Polygon'),
        ('MultiPoint', 'MultiPoint'),
        ('MultiLineString', 'MultiLineString'),
        ('MultiPolygon', 'MultiPolygon'),
        ('GeometryCollection', 'GeometryCollection'),
    ]

    PUBLISHING_STATES = [
        ('DRAFT', 'Draft'),
        ('PUBLISHED', 'Published'),
        ('FAILED', 'Failed'),
        ('UNPUBLISHED', 'Unpublished'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="Layer name")
    title = models.CharField(max_length=200, blank=True, help_text="Human-readable title")
    description = models.TextField(blank=True)

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='layers')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='layers')

    table_name = models.CharField(max_length=100, help_text="PostGIS table name")
    geometry_column = models.CharField(max_length=100, default='geom', help_text="Geometry column name")
    geometry_type = models.CharField(max_length=50, choices=GEOMETRY_TYPES, help_text="Geometry type")
    srid = models.IntegerField(default=4326, help_text="Spatial Reference System Identifier")

    publishing_state = models.CharField(
        max_length=20,
        choices=PUBLISHING_STATES,
        default='DRAFT',
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        app_label = 'geodata_providers'
        verbose_name = "Layer"
        verbose_name_plural = "Layers"
        ordering = ['workspace__name', 'name']
        unique_together = ['workspace', 'name']

    def __str__(self):
        return f"{self.workspace.name}/{self.name}"

    @property
    def full_table_name(self):
        """Returns fully qualified table name: schema.table."""
        return f"{self.store.schema}.{self.table_name}"

    @property
    def is_published(self):
        """Check if layer is currently published."""
        return self.publishing_state == 'PUBLISHED'


class Style(models.Model):
    """
    Provider-owned style definition backed by GeoServer SLD or MBStyle content.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    geodata_engine = models.ForeignKey(
        GeodataEngine,
        on_delete=models.CASCADE,
        related_name='styles',
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='styles',
        null=True,
        blank=True,
        help_text="Workspace-scoped style. Leave blank for a global engine style.",
    )
    name = models.CharField(max_length=100, help_text="GeoServer style identifier")
    title = models.CharField(max_length=200, blank=True, help_text="Human-readable title")
    description = models.TextField(blank=True)
    format = models.CharField(max_length=20, choices=STYLE_FORMATS)
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
        choices=VALIDATION_STATES,
        default="UNKNOWN",
    )
    validation_errors = models.JSONField(default=list, blank=True)
    remote_state = models.CharField(
        max_length=20,
        choices=REMOTE_STATES,
        default="LOCAL_ONLY",
    )
    remote_error = models.TextField(blank=True)
    remote_uploaded_at = models.DateTimeField(null=True, blank=True)
    remote_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        app_label = 'geodata_providers'
        verbose_name = "Style"
        verbose_name_plural = "Styles"
        ordering = ['geodata_engine__name', 'workspace__name', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['geodata_engine', 'workspace', 'name'],
                condition=models.Q(workspace__isnull=False),
                name='unique_style_per_engine_workspace_name',
            ),
            models.UniqueConstraint(
                fields=['geodata_engine', 'name'],
                condition=models.Q(workspace__isnull=True),
                name='unique_global_style_per_engine_name',
            ),
        ]

    def __str__(self):
        return self.qualified_name

    def clean(self):
        super().clean()
        if self.workspace and self.workspace.geodata_engine_id != self.geodata_engine_id:
            raise ValidationError({
                'workspace': 'Style workspace must belong to the selected geodata engine.'
            })
        if self.validation_errors is None:
            self.validation_errors = []

    def save(self, *args, **kwargs):
        self.content_hash = hashlib.sha256((self.file_content or '').encode('utf-8')).hexdigest()
        self.clean()
        super().save(*args, **kwargs)

    @property
    def is_global(self):
        return self.workspace_id is None

    @property
    def is_valid(self):
        return self.validation_state == "VALID"

    @property
    def is_synced(self):
        return self.remote_state == "SYNCED"

    @property
    def is_remote_supported(self):
        return self.remote_state != "UNSUPPORTED"

    @property
    def qualified_name(self):
        if self.workspace:
            return f"{self.workspace.name}:{self.name}"
        return self.name


class LayerStyleAssignment(models.Model):
    """
    Technical assignment of a style to a layer as default or alternate.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    layer = models.ForeignKey(
        Layer,
        on_delete=models.CASCADE,
        related_name='style_assignments',
    )
    style = models.ForeignKey(
        Style,
        on_delete=models.CASCADE,
        related_name='layer_assignments',
    )
    role = models.CharField(max_length=20, choices=LAYER_STYLE_ROLES, default='default')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        app_label = 'geodata_providers'
        db_table = 'geodata_providers_layerstyle'
        verbose_name = "Layer Style Assignment"
        verbose_name_plural = "Layer Style Assignments"
        ordering = ['layer__workspace__name', 'layer__name', 'role', 'style__name']
        constraints = [
            models.UniqueConstraint(
                fields=['layer'],
                condition=models.Q(role='default', is_active=True),
                name='unique_active_default_style_per_layer',
            ),
            models.UniqueConstraint(
                fields=['layer', 'style', 'role'],
                name='unique_layer_style_role',
            ),
        ]

    def __str__(self):
        return f"{self.layer} -> {self.style} ({self.role})"

    def clean(self):
        super().clean()
        if not self.layer_id or not self.style_id:
            return

        if self.style.validation_state == "INVALID":
            raise ValidationError({
                'style': 'Invalid styles cannot be assigned to layers.'
            })

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
