from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.admin import GeodataEngineForm
from tosca_api.apps.geodata_providers.api.serializers import GeodataEngineSerializer
from tosca_api.apps.geodata_providers.engine_factory import EngineClientFactory
from tosca_api.apps.geodata_providers.exceptions import UnsupportedEngineError
from tosca_api.apps.geodata_providers.geoserver.client import GeoServerClient
from tosca_api.apps.geodata_providers.models import GeodataEngine
from tosca_api.apps.geodata_providers.sync_service import GeoServerSyncService


class EngineClientFactoryTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='factory-user', password='testpass123')

    def _make_engine(self, engine_type: str) -> GeodataEngine:
        return GeodataEngine.objects.create(
            name=f'{engine_type}-engine',
            description='test',
            engine_type=engine_type,
            base_url='http://example.com/engine',
            public_url='http://example.com/engine',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )

    def test_create_client_returns_geoserver_client_for_geoserver_engine(self):
        engine = self._make_engine('geoserver')

        client = EngineClientFactory.create_client(engine)

        self.assertIsInstance(client, GeoServerClient)

    def test_create_sync_service_returns_geoserver_sync_service_for_geoserver_engine(self):
        engine = self._make_engine('geoserver')

        service = EngineClientFactory.create_sync_service(engine)

        self.assertIsInstance(service, GeoServerSyncService)

    def test_create_client_raises_unsupported_engine_error_for_martin(self):
        engine = self._make_engine('martin')

        with self.assertRaises(UnsupportedEngineError):
            EngineClientFactory.create_client(engine)

    def test_create_sync_service_raises_unsupported_engine_error_for_martin(self):
        engine = self._make_engine('martin')

        with self.assertRaises(UnsupportedEngineError):
            EngineClientFactory.create_sync_service(engine)

    def test_create_client_raises_unsupported_engine_error_for_pg_tileserv(self):
        engine = self._make_engine('pg_tileserv')

        with self.assertRaises(UnsupportedEngineError):
            EngineClientFactory.create_client(engine)

    def test_create_sync_service_raises_unsupported_engine_error_for_pg_tileserv(self):
        engine = self._make_engine('pg_tileserv')

        with self.assertRaises(UnsupportedEngineError):
            EngineClientFactory.create_sync_service(engine)


class EngineTypeChoiceHidingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='choice-user', password='testpass123')

    def test_admin_form_hides_martin_for_new_engine(self):
        form = GeodataEngineForm()

        choice_values = {value for value, _ in form.fields['engine_type'].choices}

        self.assertIn('geoserver', choice_values)
        self.assertNotIn('martin', choice_values)
        self.assertNotIn('pg_tileserv', choice_values)

    def test_admin_form_keeps_existing_martin_engine_editable(self):
        engine = GeodataEngine.objects.create(
            name='legacy-martin',
            description='test',
            engine_type='martin',
            base_url='http://example.com/engine',
            public_url='http://example.com/engine',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )

        form = GeodataEngineForm(instance=engine)

        choice_values = {value for value, _ in form.fields['engine_type'].choices}
        self.assertIn('martin', choice_values)

    def test_serializer_rejects_martin_for_new_engine(self):
        serializer = GeodataEngineSerializer(data={
            'name': 'new-martin',
            'description': 'test',
            'engine_type': 'martin',
            'base_url': 'http://example.com/engine',
            'public_url': 'http://example.com/engine',
            'admin_username': 'admin',
            'admin_password': 'secret',
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('engine_type', serializer.errors)
