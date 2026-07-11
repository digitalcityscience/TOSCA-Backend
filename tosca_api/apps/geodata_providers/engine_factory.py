"""Engine client factory for geoengine API workflows."""

from .exceptions import UnsupportedEngineError
from .geoserver.client import GeoServerClient
from .models import GeodataEngine
from .sync_service import GeoServerSyncService


class EngineClientFactory:
    @staticmethod
    def create_client(engine: GeodataEngine):
        if engine.engine_type == 'geoserver':
            return GeoServerClient(
                url=engine.engine_url,
                username=engine.admin_username,
                password=engine.decrypted_admin_password,
            )

        raise UnsupportedEngineError(f"Unsupported engine type: {engine.engine_type}")

    @staticmethod
    def create_sync_service(engine: GeodataEngine):
        if engine.engine_type == 'geoserver':
            return GeoServerSyncService(engine)

        raise UnsupportedEngineError(f"Unsupported engine type: {engine.engine_type}")
