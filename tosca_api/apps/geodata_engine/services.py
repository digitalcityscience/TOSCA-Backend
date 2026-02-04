"""
Geodata Engine Service
Main orchestrator for all geodata operations following claude.md specifications
"""
import logging
import os
from typing import Dict, List, Optional, Tuple
from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from .models import Workspace, Store, Layer
from .exceptions import (
    GeodataEngineError, 
    PublishingError, 
    DataImportError, 
    DatabaseIntrospectionError
)

logger = logging.getLogger(__name__)

class GeodataEngineService:
    """
    Main service for geodata engine operations
    Simplified version focusing on basic operations
    """
    
    def __init__(self):
        """Initialize the service"""
        logger.info("GeodataEngineService initialized")
    
    def upload_and_import_layer(
        self,
        workspace_id: str,
        layer_name: str,
        uploaded_file: UploadedFile,
        store_id: str,
        user: User,
        title: str = "",
        description: str = ""
    ) -> Layer:
        """
        Handle file upload and import to PostGIS
        Simplified version - will implement full functionality later
        """
        logger.info(f"Starting upload and import: workspace_id={workspace_id}, layer={layer_name}")
        
        # Get workspace and store
        try:
            workspace = Workspace.objects.get(id=workspace_id)
            store = Store.objects.get(id=store_id)
        except (Workspace.DoesNotExist, Store.DoesNotExist) as e:
            raise DataImportError(f"Workspace or Store not found: {e}")
        
        # For now, create a basic layer record
        # TODO: Implement actual file processing and PostGIS import
        layer = Layer.objects.create(
            name=layer_name,
            title=title or layer_name,
            description=description,
            workspace=workspace,
            store=store,
            table_name=self._sanitize_table_name(layer_name),
            geometry_column='geom',
            geometry_type='Point',  # Default
            srid=4326,
            publishing_state='unpublished',
            created_by=user
        )
        
        logger.info(f"Created layer: {layer}")
        return layer
    
    def _sanitize_table_name(self, name: str) -> str:
        """
        Sanitize name for use as PostGIS table name
        """
        import re
        
        # Convert to lowercase and replace invalid characters
        sanitized = re.sub(r'[^a-z0-9_]', '_', name.lower())
        
        # Ensure it starts with a letter
        if sanitized and sanitized[0].isdigit():
            sanitized = f"layer_{sanitized}"
        
        # Ensure it's not empty
        if not sanitized:
            sanitized = "layer"
        
        return sanitized
    
    def ensure_workspace_in_geoserver(self, workspace_name: str) -> Dict:
        """
        Ensure workspace exists in GeoServer
        
        Args:
            workspace_name: Workspace name
            
        Returns:
            Dict with success status and details
        """
        logger.info(f"Ensuring workspace '{workspace_name}' exists in GeoServer")
        
        try:
            # Check if workspace already exists
            if self.publisher.workspace_exists(workspace_name):
                logger.info(f"Workspace '{workspace_name}' already exists in GeoServer")
                return {
                    'success': True,
                    'workspace': workspace_name,
                    'message': f"Workspace '{workspace_name}' already exists",
                    'created': False
                }
            
            # Create workspace in GeoServer
            result = self.publisher.create_workspace(workspace_name)
            logger.info(f"Created workspace '{workspace_name}' in GeoServer")
            
            result['created'] = True
            return result
            
        except Exception as e:
            error_msg = f"Failed to create workspace '{workspace_name}' in GeoServer: {e}"
            logger.error(error_msg)
            raise GeodataEngineError(error_msg)
    
    def ensure_store_in_geoserver(self, store: Store, workspace_name: str) -> Dict:
        """
        Ensure PostGIS store exists in GeoServer workspace
        
        Args:
            store: Store instance from Django model
            workspace_name: Target workspace name
            
        Returns:
            Dict with success status and details
        """
        logger.info(f"Ensuring store '{store.name}' exists in GeoServer workspace '{workspace_name}'")
        
        try:
            # First ensure the workspace exists
            self.ensure_workspace_in_geoserver(workspace_name)
            
            # Create the PostGIS store in GeoServer
            result = self.publisher.create_postgis_store(
                workspace=workspace_name,
                store_name=store.name,
                host=store.host,
                port=store.port,
                database=store.database,
                username=store.username,
                password=store.password,
                schema=store.schema
            )
            
            logger.info(f"Successfully ensured store '{store.name}' in workspace '{workspace_name}'")
            return result
            
        except Exception as e:
            error_msg = f"Failed to ensure store '{store.name}' in GeoServer workspace '{workspace_name}': {e}"
            logger.error(error_msg)
            raise GeodataEngineError(error_msg)