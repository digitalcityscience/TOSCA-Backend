"""
Plugin architecture for different GeodataEngine types
Supports GeoServer, Martin, pg_tileserv, etc.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from django.contrib.admin import SimpleListFilter
from django.contrib import admin


class EnginePlugin(ABC):
    """Base plugin for GeodataEngine types"""
    
    @property
    @abstractmethod
    def engine_type(self) -> str:
        """Engine type identifier (e.g., 'geoserver', 'martin')"""
        pass
    
    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable engine type name"""
        pass
    
    @abstractmethod
    def get_admin_actions(self) -> Dict[str, callable]:
        """Return admin actions specific to this engine type"""
        pass
    
    @abstractmethod
    def get_sync_service(self, engine):
        """Return sync service instance for this engine"""
        pass
    
    @abstractmethod
    def get_additional_fields(self) -> List[str]:
        """Additional model fields specific to this engine type"""
        pass
    
    def get_list_filters(self) -> List[SimpleListFilter]:
        """Additional admin filters for this engine type"""
        return []
    
    def validate_connection(self, engine) -> Dict[str, Any]:
        """Validate connection to the engine"""
        return {'valid': True, 'message': 'Connection successful'}


class GeoServerPlugin(EnginePlugin):
    """Plugin for GeoServer engines"""
    
    @property
    def engine_type(self) -> str:
        return 'geoserver'
    
    @property
    def display_name(self) -> str:
        return 'GeoServer'
    
    def get_admin_actions(self) -> Dict[str, callable]:
        from .actions import sync_with_geoserver, test_geoserver_connection
        return {
            'sync_with_geoserver': sync_with_geoserver,
            'test_geoserver_connection': test_geoserver_connection,
        }
    
    def get_sync_service(self, engine):
        from .sync_service import GeoServerSyncService
        return GeoServerSyncService(engine)
    
    def get_additional_fields(self) -> List[str]:
        return ['admin_username', 'admin_password']
    
    def validate_connection(self, engine) -> Dict[str, Any]:
        try:
            import requests
            response = requests.get(f"{engine.base_url}/web/", timeout=10)
            return {
                'valid': response.status_code == 200,
                'message': f'GeoServer responded with status {response.status_code}'
            }
        except Exception as e:
            return {'valid': False, 'message': str(e)}


class MartinPlugin(EnginePlugin):
    """Plugin for Martin tile servers (future implementation)"""
    
    @property
    def engine_type(self) -> str:
        return 'martin'
    
    @property
    def display_name(self) -> str:
        return 'Martin Tiles'
    
    def get_admin_actions(self) -> Dict[str, callable]:
        # TODO: Implement Martin-specific actions
        return {}
    
    def get_sync_service(self, engine):
        # TODO: Implement Martin sync service
        pass
    
    def get_additional_fields(self) -> List[str]:
        return ['api_key']  # Different auth than GeoServer


# Plugin Registry
class PluginRegistry:
    """Registry for all engine plugins"""
    
    def __init__(self):
        self._plugins: Dict[str, EnginePlugin] = {}
        
        # Register default plugins
        self.register(GeoServerPlugin())
        # self.register(MartinPlugin())  # Future
    
    def register(self, plugin: EnginePlugin):
        """Register a new plugin"""
        self._plugins[plugin.engine_type] = plugin
    
    def get_plugin(self, engine_type: str) -> EnginePlugin:
        """Get plugin by engine type"""
        return self._plugins.get(engine_type)
    
    def get_all_plugins(self) -> Dict[str, EnginePlugin]:
        """Get all registered plugins"""
        return self._plugins.copy()
    
    def get_engine_choices(self):
        """Get choices for engine type field"""
        return [(key, plugin.display_name) for key, plugin in self._plugins.items()]


# Global plugin registry instance
plugin_registry = PluginRegistry()