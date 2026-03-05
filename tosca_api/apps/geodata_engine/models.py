from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from .encryption import EncryptedCharField
import uuid


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
        verbose_name = "Geodata Engine Instance"
        verbose_name_plural = "Geodata Engine Instances"
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
    published_url = models.URLField(blank=True, help_text="Published layer URL (WFS/WMS)")
    publishing_error = models.TextField(blank=True, help_text="Last publishing error message")
    published_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
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
