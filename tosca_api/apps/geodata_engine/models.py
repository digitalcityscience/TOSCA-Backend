from django.db import models
from django.contrib.auth.models import User
from .encryption import EncryptedCharField, encrypt_value, decrypt_value
import uuid
import logging

logger = logging.getLogger(__name__)


class GeodataEngine(models.Model, EncryptedCharField):
    """
    GeoServer-powered Geodata Engine 
    Contains GeoServer connection details and manages workspaces, stores, layers
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=100, 
        unique=True, 
        help_text="Engine name (e.g., 'Default GeoServer', 'Production Instance')"
    )
    description = models.TextField(
        blank=True, 
        help_text="Description of this GeoServer instance"
    )
    
    # GeoServer connection details
    base_url = models.CharField(
        max_length=255,
        help_text="Full URL to GeoServer (e.g., 'http://geoserver:8080/geoserver')"
    )
    admin_username = models.CharField(
        max_length=100,
        default='admin2',
        help_text="GeoServer admin username"
    )
    admin_password = models.CharField(
        max_length=100,
        default='geoserver2',
        help_text="GeoServer admin password"
    )
    
    # Status
    is_active = models.BooleanField(
        default=True, 
        help_text="Is this GeoServer instance active?"
    )
    is_default = models.BooleanField(
        default=False, 
        help_text="Is this the default GeoServer instance?"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "GeoServer Instance"
        verbose_name_plural = "GeoServer Instances"
        ordering = ['-is_default', 'name']

    def __str__(self):
        default_marker = " (Default)" if self.is_default else ""
        return f"{self.name}{default_marker}"

    def save(self, *args, **kwargs):
        # TODO: Encrypt password before saving (temporarily disabled due to length issue)
        # if self.admin_password:
        #     self.admin_password = self.encrypt_field('admin_password', self.admin_password)
        
        # Ensure only one default engine
        if self.is_default:
            GeodataEngine.objects.filter(is_default=True).update(is_default=False)
        super().save(*args, **kwargs)
    
    @property
    def decrypted_admin_password(self):
        """Get decrypted admin password"""
        return self.decrypt_field('admin_password', self.admin_password)

    @property
    def geoserver_url(self):
        """Get GeoServer URL"""
        return self.base_url


class Workspace(models.Model):
    """
    Logical grouping of data (e.g. 'mobility', 'environment')
    Belongs to a specific GeodataEngine
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    geodata_engine = models.ForeignKey(GeodataEngine, on_delete=models.CASCADE, related_name='workspaces', null=True, blank=True)
    name = models.CharField(max_length=100, help_text="Workspace name (e.g., 'mobility', 'environment')")
    description = models.TextField(blank=True, help_text="Description of this workspace")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    
    class Meta:
        verbose_name = "Workspace"
        verbose_name_plural = "Workspaces"
        ordering = ['name']
        unique_together = [['geodata_engine', 'name']]  # Unique name per engine
    
    def __str__(self):
        return f"{self.geodata_engine.name} → {self.name}"
    
    def save(self, *args, **kwargs):
        # Create in GeoServer when saving
        is_new = self._state.adding
        super().save(*args, **kwargs)
        
        if is_new:
            self._create_in_geoserver()
    
    def _create_in_geoserver(self):
        """Create workspace in GeoServer"""
        try:
            from .geoserver.client import GeoServerClient
            client = GeoServerClient(
                url=self.geodata_engine.geoserver_url,
                username=self.geodata_engine.admin_username,
                password=self.geodata_engine.decrypted_admin_password
            )
            result = client.create_workspace(self.name)
            logger.info(f"Created workspace {self.name} in GeoServer: {result}")
        except Exception as e:
            logger.error(f"Failed to create workspace {self.name} in GeoServer: {e}")


class Store(models.Model, EncryptedCharField):
    """
    Represents a PostGIS connection + schema
    Belongs to a specific GeodataEngine
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    geodata_engine = models.ForeignKey(GeodataEngine, on_delete=models.CASCADE, related_name='stores', null=True, blank=True)
    workspace = models.ForeignKey('Workspace', on_delete=models.CASCADE, related_name='stores', null=True, blank=True, help_text="Workspace this store belongs to")
    name = models.CharField(max_length=100, help_text="Store name for identification")
    host = models.CharField(max_length=255, help_text="PostGIS host")
    port = models.IntegerField(default=5432, help_text="PostGIS port")
    database = models.CharField(max_length=100, help_text="PostGIS database name")
    username = models.CharField(max_length=100, help_text="PostGIS username")
    password = models.CharField(max_length=100, help_text="PostGIS password")
    schema = models.CharField(
        max_length=100, 
        default="public",
        help_text="PostGIS schema (e.g., 'public', 'gis', 'mobility')"
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    
    class Meta:
        verbose_name = "Store"
        verbose_name_plural = "Stores"
        ordering = ['name']
        unique_together = [['workspace', 'name']]  # Unique name per workspace
    
    def __str__(self):
        return f"{self.geodata_engine.name} → {self.name}"
    
    def save(self, *args, **kwargs):
        # TODO: Encrypt password before saving (temporarily disabled due to length issue)
        # if self.password:
        #     self.password = self.encrypt_field('password', self.password)
        
        # Create in GeoServer when saving
        is_new = self._state.adding
        super().save(*args, **kwargs)
        
        if is_new:
            self._create_in_geoserver()
    
    def _create_in_geoserver(self):
        """Create store in GeoServer"""
        try:
            from .geoserver.client import GeoServerClient
            client = GeoServerClient(
                url=self.geodata_engine.geoserver_url,
                username=self.geodata_engine.admin_username,
                password=self.geodata_engine.decrypted_admin_password
            )
            
            store_data = {
                'name': self.name,
                'host': self.host,
                'port': self.port,
                'database': self.database,
                'user': self.username,
                'passwd': self.decrypted_password,
                'schema': self.schema
            }
            
            result = client.create_store(workspace=self.workspace.name, store_data=store_data)
            logger.info(f"Created store {self.name} in GeoServer: {result}")
        except Exception as e:
            logger.error(f"Failed to create store {self.name} in GeoServer: {e}")
    
    @property 
    def decrypted_password(self):
        """Get decrypted password"""
        return self.decrypt_field('password', self.password)


class Layer(models.Model):
    """
    Logical dataset backed by a PostGIS table or view
    Created via file upload (GeoJSON, GeoPackage) or database introspection
    Publishing is explicit, never implicit
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
        ('unpublished', 'Unpublished'),
        ('published', 'Published'),
        ('error', 'Publishing Error'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="Layer name")
    title = models.CharField(max_length=200, blank=True, help_text="Human-readable title")
    description = models.TextField(blank=True)
    
    # Core relationships
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='layers')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='layers')
    
    # PostGIS table info
    table_name = models.CharField(max_length=100, help_text="PostGIS table name")
    geometry_column = models.CharField(max_length=100, default='geom', help_text="Geometry column name")
    geometry_type = models.CharField(max_length=50, choices=GEOMETRY_TYPES, help_text="Geometry type")
    srid = models.IntegerField(default=4326, help_text="Spatial Reference System Identifier")
    
    # Publishing state
    publishing_state = models.CharField(
        max_length=20, 
        choices=PUBLISHING_STATES, 
        default='unpublished',
        help_text="Current publishing state"
    )
    published_url = models.URLField(blank=True, help_text="Published layer URL (WFS/WMS)")
    publishing_error = models.TextField(blank=True, help_text="Last publishing error message")
    published_at = models.DateTimeField(null=True, blank=True)
    
    # Metadata
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
        """Returns fully qualified table name: schema.table"""
        return f"{self.store.schema}.{self.table_name}"
    
    @property
    def is_published(self):
        """Check if layer is currently published"""
        return self.publishing_state == 'published'