"""
GeoServer Publishing Engine Implementation
Following claude.md specifications for GeoServer as POC engine
"""
import logging
from typing import Dict, Optional
from django.conf import settings
from ..exceptions import GeoServerConnectionError, GeoServerPublishError
from .client import GeoServerClient

logger = logging.getLogger(__name__)


class GeoServerEngine:
    """
    GeoServer implementation for publishing
    Simplified version without abstract base class
    """
    
    def __init__(
        self,
        url: str = None,
        username: str = None,
        password: str = None
    ):
        """
        Initialize GeoServer engine
        
        Args:
            url: GeoServer URL (defaults from settings)
            username: GeoServer username (defaults from settings)
            password: GeoServer password (defaults from settings)
        """
        self.url = url or self._get_geoserver_url()
        self.username = username or getattr(settings, 'GEOSERVER_ADMIN_USER', 'admin2')
        self.password = password or getattr(settings, 'GEOSERVER_ADMIN_PASSWORD', 'geoserver2')
        
        self.client = GeoServerClient(
            url=self.url,
            username=self.username,
            password=self.password
        )
        logger.info(f"GeoServerEngine initialized for {self.url}")
    
    def _get_geoserver_url(self) -> str:
        """Get GeoServer URL from settings"""
        host = getattr(settings, 'GEOSERVER_HOST', 'geoserver')
        port = getattr(settings, 'GEOSERVER_PORT', '8080')
        return f"http://{host}:{port}/geoserver"
    
    def create_workspace(self, name: str, description: str = "") -> Dict:
        """
        Create workspace in GeoServer
        
        Args:
            name: Workspace name
            description: Workspace description (ignored by GeoServer REST API)
            
        Returns:
            Dict with creation result
        """
        logger.info(f"Creating workspace: {name}")
        
        # Check if workspace already exists
        if self.client.workspace_exists(name):
            logger.info(f"Workspace {name} already exists")
            return {
                'success': True,
                'workspace': name,
                'message': f"Workspace '{name}' already exists",
                'created': False
            }
        
        result = self.client.create_workspace(name)
        result['created'] = True
        return result
    
    def create_datastore(
        self,
        workspace: str,
        store_name: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        schema: str = "public"
    ) -> Dict:
        """
        Create PostGIS datastore in GeoServer
        
        Args:
            workspace: Target workspace
            store_name: Store name
            host: PostGIS host
            port: PostGIS port
            database: PostGIS database
            username: PostGIS username
            password: PostGIS password
            schema: PostGIS schema
            
        Returns:
            Dict with creation result
        """
        logger.info(f"Creating PostGIS datastore: {workspace}/{store_name}")
        
        # Ensure workspace exists
        self.create_workspace(workspace)
        
        # Check if store already exists
        if self.client.store_exists(workspace, store_name):
            logger.info(f"Store {workspace}/{store_name} already exists")
            return {
                'success': True,
                'workspace': workspace,
                'store': store_name,
                'message': f"PostGIS store '{store_name}' already exists in workspace '{workspace}'",
                'created': False
            }
        
        # Create PostGIS store using client
        try:
            result = self.client.create_postgis_store(
                name=store_name,
                workspace=workspace,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                schema=schema
            )
            
            result.update({
                'workspace': workspace,
                'store': store_name,
                'created': True
            })
            return result
            
        except Exception as e:
            error_msg = f"Failed to create PostGIS store '{store_name}': {e}"
            logger.error(error_msg)
            raise GeoServerPublishError(error_msg)
        
        result = self.client.create_postgis_store(
            name=store_name,
            workspace=workspace,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            schema=schema
        )
        result['created'] = True
        return result
    
    def publish_layer(
        self,
        workspace: str,
        store_name: str,
        layer_name: str,
        table_name: str,
        geometry_column: str = "geom",
        srid: int = 4326,
        geometry_type: str = "Point"
    ) -> Dict:
        """
        Publish layer from PostGIS table
        
        Args:
            workspace: Target workspace
            store_name: PostGIS datastore name
            layer_name: Layer name for publishing
            table_name: PostGIS table name
            geometry_column: Geometry column name (not directly used by geoserver-rest)
            srid: Spatial Reference System ID
            geometry_type: Geometry type
            
        Returns:
            Dict with publishing result including URLs
        """
        logger.info(f"Publishing layer: {workspace}/{layer_name} from table {table_name}")
        
        return self.client.publish_featuretype(
            store_name=store_name,
            workspace=workspace,
            pg_table=table_name,
            srid=srid,
            geometry_type=geometry_type,
            layer_name=layer_name
        )
    
    def unpublish_layer(self, workspace: str, layer_name: str) -> bool:
        """
        Remove published layer from GeoServer
        
        Args:
            workspace: Workspace name
            layer_name: Layer name
            
        Returns:
            True if successfully unpublished
        """
        logger.info(f"Unpublishing layer: {workspace}/{layer_name}")
        
        try:
            result = self.client.delete_layer(workspace, layer_name)
            return result.get('success', False)
        except GeoServerPublishError:
            logger.warning(f"Failed to unpublish layer {workspace}/{layer_name}")
            return False
    
    def get_layer_info(self, workspace: str, layer_name: str) -> Optional[Dict]:
        """
        Get published layer information
        
        Args:
            workspace: Workspace name
            layer_name: Layer name
            
        Returns:
            Dict with layer info or None if not found
        """
        return self.client.get_layer_info(workspace, layer_name)
    
    def workspace_exists(self, workspace: str) -> bool:
        """
        Check if workspace exists in GeoServer
        
        Args:
            workspace: Workspace name
            
        Returns:
            True if workspace exists
        """
        return self.client.workspace_exists(workspace)
    
    def create_postgis_store(
        self,
        workspace: str,
        store_name: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        schema: str = "public"
    ) -> Dict:
        """
        Alias for create_datastore - create PostGIS store in GeoServer
        
        Args:
            workspace: Target workspace
            store_name: Store name
            host: PostGIS host
            port: PostGIS port
            database: PostGIS database
            username: PostGIS username
            password: PostGIS password
            schema: PostGIS schema
            
        Returns:
            Dict with creation result
        """
        return self.create_datastore(
            workspace=workspace,
            store_name=store_name,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            schema=schema
        )