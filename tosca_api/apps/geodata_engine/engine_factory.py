"""Engine client factory for geoengine API workflows."""

from typing import Dict

from .exceptions import GeodataEngineError
from .geoserver.client import GeoServerClient
from .models import GeodataEngine
from .sync_service import GeoServerSyncService


class MartinClientPlaceholder:
    """Placeholder for future Martin integration."""

    def __init__(self, engine: GeodataEngine):
        self.engine = engine

    def _not_implemented(self) -> Dict:
        return {
            'success': False,
            'not_implemented': True,
            'engine_type': 'martin',
            'message': 'Martin client is not implemented yet',
        }

    def get_workspaces(self):
        return []

    def create_workspace(self, name: str):
        return self._not_implemented()

    def delete_workspace(self, name: str):
        return self._not_implemented()

    def create_store(self, workspace: str, store_data: dict):
        return self._not_implemented()

    def publish_featuretype(self, **kwargs):
        return self._not_implemented()

    def delete_layer(self, workspace: str, layer_name: str):
        return self._not_implemented()


class MartinSyncPlaceholder:
    """Placeholder for future Martin sync implementation."""

    def __init__(self, engine: GeodataEngine):
        self.engine = engine

    def sync_all_resources(self, created_by) -> Dict:
        return {
            'success': False,
            'not_implemented': True,
            'engine_type': 'martin',
            'message': 'Martin sync is not implemented yet',
        }


class EngineClientFactory:
    @staticmethod
    def create_client(engine: GeodataEngine):
        if engine.engine_type == 'geoserver':
            return GeoServerClient(
                url=engine.engine_url,
                username=engine.admin_username,
                password=engine.decrypted_admin_password,
            )

        if engine.engine_type == 'martin':
            return MartinClientPlaceholder(engine)

        raise GeodataEngineError(f"Unsupported engine type: {engine.engine_type}")

    @staticmethod
    def create_sync_service(engine: GeodataEngine):
        if engine.engine_type == 'geoserver':
            return GeoServerSyncService(engine)

        if engine.engine_type == 'martin':
            return MartinSyncPlaceholder(engine)

        raise GeodataEngineError(f"Unsupported engine type: {engine.engine_type}")
