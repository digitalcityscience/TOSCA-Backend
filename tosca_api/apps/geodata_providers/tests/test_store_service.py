from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerStyleAssignment,
    Store,
    Style,
    Workspace,
)
from tosca_api.apps.geodata_providers.api.views import StoreViewSet
from tosca_api.apps.geodata_providers.postgis_inspector import PostGISInspectorError
from tosca_api.apps.geodata_providers.services.commands.store_service import StoreService


class StoreServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='store-service-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Store Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='demo_ws',
            description='workspace',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='source_store',
            description='store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            created_by=self.user,
        )

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.test_postgis_connection')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_create_postgis_store_persists_after_validation_and_remote_create(
        self,
        mock_create_client,
        mock_test_postgis_connection,
    ):
        mock_test_postgis_connection.return_value = {
            'host': 'db',
            'port': 5432,
            'database': 'gis',
            'username': 'postgres',
            'schema': 'public',
            'schema_exists': True,
        }
        client = mock_create_client.return_value
        client.create_postgis_store.return_value = {'success': True, 'verified': True, 'message': 'created'}

        result = StoreService.create_postgis_store(
            workspace=self.workspace,
            name='new_store',
            user=self.user,
            store_type='postgis',
            description='desc',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
        )

        self.assertTrue(result['success'])
        self.assertTrue(result['created'])
        self.assertTrue(Store.objects.filter(workspace=self.workspace, name='new_store').exists())
        mock_test_postgis_connection.assert_called_once_with(
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
        )
        client.create_postgis_store.assert_called_once_with(
            name='new_store',
            workspace='demo_ws',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
        )

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.test_postgis_connection')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_create_postgis_store_blocks_remote_create_when_validation_fails(
        self,
        mock_create_client,
        mock_test_postgis_connection,
    ):
        mock_test_postgis_connection.side_effect = PostGISInspectorError('bad credentials')

        result = StoreService.create_postgis_store(
            workspace=self.workspace,
            name='invalid_store',
            user=self.user,
            store_type='postgis',
            description='desc',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='wrong',
            schema='public',
        )

        self.assertFalse(result['success'])
        self.assertFalse(Store.objects.filter(workspace=self.workspace, name='invalid_store').exists())
        mock_create_client.assert_not_called()

    def test_test_store_connection_requires_password_for_postgis(self):
        result = StoreService.test_store_connection(
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='',
            schema='public',
        )

        self.assertFalse(result['success'])
        self.assertIn('password', result['details']['field_errors'])

    def test_test_store_connection_skips_non_postgis_store(self):
        result = StoreService.test_store_connection(store_type='file')

        self.assertTrue(result['success'])
        self.assertTrue(result['skipped'])

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_sync_service')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.test_postgis_connection')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_clone_store_returns_sync_result(
        self,
        mock_create_client,
        mock_test_postgis_connection,
        mock_create_sync_service,
    ):
        mock_test_postgis_connection.return_value = {'schema_exists': True}
        mock_create_client.return_value.create_postgis_store.return_value = {
            'success': True,
            'verified': True,
            'message': 'created',
        }
        mock_create_sync_service.return_value.sync_stores_for_workspace.return_value = {
            'success': True,
            'created': 1,
            'errors': [],
        }

        result = StoreService.clone_store(
            source_store=self.store,
            target_workspace=self.workspace,
            name='cloned_store',
            user=self.user,
            description='clone',
            password='secret',
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['sync_result']['created'], 1)
        self.assertTrue(Store.objects.filter(workspace=self.workspace, name='cloned_store').exists())

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_sync_service')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.test_postgis_connection')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_clone_store_copies_layers_and_valid_style_assignments_when_requested(
        self,
        mock_create_client,
        mock_test_postgis_connection,
        mock_create_sync_service,
    ):
        source_layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='roads',
            title='Roads',
            description='Road network',
            table_name='roads_table',
            geometry_column='geom',
            geometry_type='LineString',
            srid=4326,
            publishing_state='PUBLISHED',
            is_public=True,
            created_by=self.user,
        )
        style = Style.objects.create(
            geodata_engine=self.engine,
            workspace=None,
            name='roads_default',
            title='Roads Default',
            format='sld',
            file_content='<StyledLayerDescriptor />',
            validation_state='VALID',
            remote_state='SYNCED',
            created_by=self.user,
        )
        LayerStyleAssignment.objects.create(
            layer=source_layer,
            style=style,
            role='default',
            is_active=True,
            created_by=self.user,
        )
        mock_test_postgis_connection.return_value = {'schema_exists': True}
        client = mock_create_client.return_value
        client.create_postgis_store.return_value = {
            'success': True,
            'verified': True,
            'message': 'created',
        }
        client.get_layer_info.return_value = None
        client.publish_featuretype.return_value = {'success': True}
        client.verify_featuretype.return_value = True
        mock_create_sync_service.return_value.sync_stores_for_workspace.return_value = {'errors': []}
        mock_create_sync_service.return_value.sync_layers_for_workspace.return_value = {'errors': []}

        result = StoreService.clone_store(
            source_store=self.store,
            target_workspace=self.workspace,
            name='cloned_store',
            user=self.user,
            description='clone',
            password='secret',
            clone_layers=True,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['layer_clone_result']['created'], 1)
        cloned_layer = Layer.objects.get(workspace=self.workspace, name='cloned_store_roads')
        self.assertEqual(cloned_layer.store.name, 'cloned_store')
        self.assertEqual(cloned_layer.table_name, 'roads_table')
        self.assertTrue(
            LayerStyleAssignment.objects.filter(
                layer=cloned_layer,
                style=style,
                role='default',
                is_active=True,
            ).exists()
        )
        client.publish_featuretype.assert_called_once_with(
            store_name='cloned_store',
            workspace='demo_ws',
            pg_table='roads_table',
            srid=4326,
            geometry_type='LineString',
            layer_name='cloned_store_roads',
            title='Roads',
        )

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_sync_service')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.test_postgis_connection')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_clone_store_skips_layer_copy_by_default(
        self,
        mock_create_client,
        mock_test_postgis_connection,
        mock_create_sync_service,
    ):
        Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='roads',
            title='Roads',
            table_name='roads_table',
            geometry_column='geom',
            geometry_type='LineString',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        mock_test_postgis_connection.return_value = {'schema_exists': True}
        client = mock_create_client.return_value
        client.create_postgis_store.return_value = {
            'success': True,
            'verified': True,
            'message': 'created',
        }
        mock_create_sync_service.return_value.sync_stores_for_workspace.return_value = {'errors': []}

        result = StoreService.clone_store(
            source_store=self.store,
            target_workspace=self.workspace,
            name='cloned_store',
            user=self.user,
            description='clone',
            password='secret',
        )

        self.assertTrue(result['success'])
        self.assertTrue(result['layer_clone_result']['skipped'])
        self.assertFalse(Layer.objects.filter(workspace=self.workspace, name='cloned_store_roads').exists())
        client.publish_featuretype.assert_not_called()

    def test_delete_store_safe_blocks_when_layers_exist(self):
        Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='dependent_layer',
            title='Dependent Layer',
            description='desc',
            table_name='dependent_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            created_by=self.user,
        )

        result = StoreService.delete_store_safe(self.store)

        self.assertFalse(result['success'])
        self.assertTrue(result['blocked'])
        self.assertIn('layers=1', result['message'])
        self.assertTrue(Store.objects.filter(pk=self.store.pk).exists())

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_delete_store_safe_deletes_after_remote_delete(self, mock_create_client):
        mock_create_client.return_value.delete_store.return_value = {
            'success': True,
            'verified': True,
            'already_deleted': False,
            'message': 'deleted',
        }

        result = StoreService.delete_store_safe(self.store)

        self.assertTrue(result['success'])
        self.assertFalse(Store.objects.filter(pk=self.store.pk).exists())

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.test_postgis_connection')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_update_postgis_store_connection_allows_correction_without_layers(
        self,
        mock_create_client,
        mock_test_postgis_connection,
    ):
        mock_test_postgis_connection.return_value = {'schema_exists': True}
        mock_create_client.return_value.update_postgis_store.return_value = {
            'success': True,
            'verified': True,
            'message': 'updated',
        }

        result = StoreService.update_postgis_store_connection(
            store=self.store,
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='gis_schema',
            description='corrected',
        )

        self.assertTrue(result['success'])
        self.store.refresh_from_db()
        self.assertEqual(self.store.schema, 'gis_schema')
        self.assertEqual(self.store.description, 'corrected')
        self.assertEqual(self.store.sync_state, 'SYNCED')
        mock_test_postgis_connection.assert_called_once_with(
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='gis_schema',
        )
        mock_create_client.return_value.update_postgis_store.assert_called_once_with(
            name='source_store',
            workspace='demo_ws',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='gis_schema',
        )

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.test_postgis_connection')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_update_postgis_store_connection_syncs_layers_when_layers_exist(
        self,
        mock_create_client,
        mock_test_postgis_connection,
    ):
        Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='dependent_layer',
            title='Dependent Layer',
            description='desc',
            table_name='dependent_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            created_by=self.user,
        )

        mock_test_postgis_connection.return_value = {'schema_exists': True}
        mock_create_client.return_value.update_postgis_store.return_value = {
            'success': True,
            'verified': True,
            'message': 'updated',
        }

        with patch(
            'tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_sync_service'
        ) as mock_create_sync_service:
            mock_create_sync_service.return_value.sync_layers_for_workspace.return_value = {
                'success': True,
                'synced': 1,
                'errors': [],
            }
            result = StoreService.update_postgis_store_connection(
                store=self.store,
                host='db',
                port=5432,
                database='gis',
                username='postgres',
                password='secret',
                schema='gis_schema',
                description='updated',
            )

        self.assertTrue(result['success'])
        self.assertEqual(result['layer_sync_result']['synced'], 1)
        self.store.refresh_from_db()
        self.assertEqual(self.store.schema, 'gis_schema')
        self.assertEqual(self.store.description, 'updated')

    @patch('tosca_api.apps.geodata_providers.api.views.StoreService.test_store_connection')
    def test_store_test_connection_endpoint_returns_validation_result(self, mock_test_store_connection):
        mock_test_store_connection.return_value = {
            'success': True,
            'message': 'PostGIS connection validated.',
            'details': {'schema_exists': True},
        }
        factory = APIRequestFactory()
        request = factory.post(
            '/stores/test_connection/',
            {
                'store_type': 'postgis',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'username': 'postgres',
                'password': 'secret',
                'schema': 'public',
            },
            format='json',
        )
        force_authenticate(request, user=self.user)
        view = StoreViewSet.as_view({'post': 'test_connection_config'})

        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])
        mock_test_store_connection.assert_called_once()
