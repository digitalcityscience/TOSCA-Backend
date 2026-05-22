from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerStyleAssignment,
    Store,
    Style,
    Workspace,
)
from tosca_api.apps.geodata_providers.services.commands.geodata_engine_service import GeodataEngineService
from tosca_api.apps.geodata_providers.sync_service import GeoServerSyncService


class GeodataEngineServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='service-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Service Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )

    @patch('tosca_api.apps.geodata_providers.services.commands.geodata_engine_service.EngineClientFactory.create_sync_service')
    @patch('tosca_api.apps.geodata_providers.services.commands.geodata_engine_service.EngineClientFactory.create_client')
    def test_create_engine_persists_and_triggers_sync(self, mock_create_client, mock_create_sync_service):
        mock_create_client.return_value.validate_connection.return_value = {'success': True, 'version': '2.25'}
        mock_create_sync_service.return_value.sync_all_resources.return_value = {'success': True}

        engine, sync_result = GeodataEngineService.create_engine(
            user=self.user,
            name='Created Engine',
            description='created',
            engine_type='geoserver',
            base_url='http://created.example/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            is_default=False,
        )

        self.assertTrue(GeodataEngine.objects.filter(pk=engine.pk, name='Created Engine').exists())
        self.assertTrue(sync_result['success'])
        self.assertEqual(sync_result['db_workspace_count'], 0)
        self.assertEqual(sync_result['db_store_count'], 0)
        self.assertEqual(sync_result['db_layer_count'], 0)

    def test_delete_engine_safe_blocks_when_dependencies_exist(self):
        Workspace.objects.create(
            geodata_engine=self.engine,
            name='dependent-workspace',
            description='dependency',
            created_by=self.user,
        )

        result = GeodataEngineService.delete_engine_safe(self.engine)

        self.assertFalse(result['success'])
        self.assertTrue(result['blocked'])
        self.assertIn('workspaces=1', result['message'])
        self.assertTrue(GeodataEngine.objects.filter(pk=self.engine.pk).exists())

    def test_deactivate_and_reactivate_engine(self):
        deactivate_result = GeodataEngineService.deactivate_engine(self.engine)
        self.engine.refresh_from_db()

        self.assertTrue(deactivate_result['success'])
        self.assertFalse(self.engine.is_active)

        reactivate_result = GeodataEngineService.reactivate_engine(self.engine)
        self.engine.refresh_from_db()

        self.assertTrue(reactivate_result['success'])
        self.assertTrue(self.engine.is_active)

    @patch('tosca_api.apps.geodata_providers.services.commands.geodata_engine_service.EngineClientFactory.create_sync_service')
    def test_sync_engine_skips_inactive_engine(self, mock_create_sync_service):
        self.engine.is_active = False
        self.engine.save(update_fields=['is_active'])

        result = GeodataEngineService.sync_engine(self.engine, user=self.user)

        self.assertTrue(result['success'])
        self.assertTrue(result['skipped'])
        self.assertIn('inactive', result['reason'])
        mock_create_sync_service.assert_not_called()

    def test_sync_service_returns_skipped_result_for_inactive_engine(self):
        self.engine.is_active = False
        self.engine.save(update_fields=['is_active'])
        service = GeoServerSyncService(self.engine)
        service._get_geoserver_workspaces = MagicMock()

        result = service.sync_all_resources(created_by=self.user)

        self.assertTrue(result['success'])
        self.assertTrue(result['skipped'])
        self.assertTrue(result['workspaces']['skipped'])
        service._get_geoserver_workspaces.assert_not_called()

    def test_sync_workspaces_marks_successful_resources_synced(self):
        service = GeoServerSyncService(self.engine)
        service._get_geoserver_workspaces = MagicMock(return_value=['synced_ws'])

        result = service.sync_workspaces(created_by=self.user)

        self.assertEqual(result['created'], 1)
        workspace = Workspace.objects.get(geodata_engine=self.engine, name='synced_ws')
        self.assertEqual(workspace.sync_state, 'SYNCED')
        self.assertEqual(workspace.remote_identifier, 'synced_ws')
        self.assertEqual(workspace.last_sync_error, '')
        self.assertIsNotNone(workspace.last_sync_at)

    def test_sync_workspaces_is_idempotent_for_existing_remote_workspace(self):
        Workspace.objects.create(
            geodata_engine=self.engine,
            name='synced_ws',
            description='workspace',
            created_by=self.user,
        )
        service = GeoServerSyncService(self.engine)
        service._get_geoserver_workspaces = MagicMock(return_value=['synced_ws'])

        result = service.sync_workspaces(created_by=self.user)

        self.assertEqual(result['synced'], 1)
        self.assertEqual(result['created'], 0)
        self.assertEqual(Workspace.objects.filter(geodata_engine=self.engine, name='synced_ws').count(), 1)
        workspace = Workspace.objects.get(geodata_engine=self.engine, name='synced_ws')
        self.assertEqual(workspace.sync_state, 'SYNCED')

    def test_sync_workspaces_deletes_local_workspace_missing_remotely(self):
        Workspace.objects.create(
            geodata_engine=self.engine,
            name='local_stale_ws',
            description='workspace',
            created_by=self.user,
        )
        service = GeoServerSyncService(self.engine)
        service._get_geoserver_workspaces = MagicMock(return_value=[])

        result = service.sync_workspaces(created_by=self.user)

        self.assertEqual(result['deleted'], 1)
        self.assertFalse(
            Workspace.objects.filter(geodata_engine=self.engine, name='local_stale_ws').exists()
        )

    def test_sync_workspaces_marks_local_resources_failed_on_remote_error(self):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='existing_ws',
            description='workspace',
            created_by=self.user,
        )
        service = GeoServerSyncService(self.engine)
        service._get_geoserver_workspaces = MagicMock(side_effect=Exception('GeoServer unavailable'))

        result = service.sync_workspaces(created_by=self.user)

        self.assertIn('GeoServer unavailable', result['errors'][0])
        workspace.refresh_from_db()
        self.assertEqual(workspace.sync_state, 'FAILED')
        self.assertIn('GeoServer unavailable', workspace.last_sync_error)
        self.assertIsNotNone(workspace.last_sync_at)

    def test_sync_layers_recovers_drift_and_preserves_layer_names_and_styles(self):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='sync_ws',
            description='workspace',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='sync_store',
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
        Layer.objects.create(
            workspace=workspace,
            store=store,
            name='local_only_layer',
            title='Local Only Layer',
            description='local stale layer',
            table_name='local_only_table',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        Style.objects.create(
            geodata_engine=self.engine,
            workspace=workspace,
            name='roads_default',
            title='Roads Default',
            format='sld',
            file_content='<StyledLayerDescriptor />',
            validation_state='VALID',
            remote_state='SYNCED',
            created_by=self.user,
        )
        service = GeoServerSyncService(self.engine)
        service._get_geoserver_layers = MagicMock(
            return_value=[
                {
                    'name': 'roads',
                    'store_name': 'sync_store',
                    'title': 'Road Network',
                    'table_name': 'roads_native_table',
                    'geometry_column': 'geom',
                    'geometry_type': 'LineString',
                    'srid': 4326,
                    'advertised': True,
                    'default_style_name': 'roads_default',
                }
            ]
        )

        result = service.sync_layers_for_workspace(workspace, created_by=self.user)

        self.assertEqual(result['created'], 1)
        self.assertEqual(result['deleted'], 1)
        self.assertFalse(Layer.objects.filter(workspace=workspace, name='local_only_layer').exists())
        layer = Layer.objects.get(workspace=workspace, name='roads')
        self.assertEqual(layer.table_name, 'roads_native_table')
        self.assertEqual(layer.title, 'Road Network')
        self.assertEqual(layer.sync_state, 'SYNCED')
        self.assertTrue(
            LayerStyleAssignment.objects.filter(
                layer=layer,
                style__name='roads_default',
                role='default',
                is_active=True,
            ).exists()
        )

    @patch('tosca_api.apps.geodata_providers.services.commands.geodata_engine_service.EngineClientFactory.create_client')
    def test_delete_engine_cascade_removes_tree_remote_and_db(self, mock_create_client):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='cascade-workspace',
            description='dependency',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='cascade_store',
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
        Layer.objects.create(
            workspace=workspace,
            store=store,
            name='cascade_layer',
            title='Cascade Layer',
            description='desc',
            table_name='cascade_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='DRAFT',
            created_by=self.user,
        )
        Style.objects.create(
            geodata_engine=self.engine,
            workspace=workspace,
            name='cascade_style',
            title='Cascade Style',
            format='mbstyle',
            file_name='cascade_style.mbstyle',
            file_content='{"version":8,"name":"cascade_style","layers":[]}',
            validation_state='VALID',
            remote_state='SYNCED',
            created_by=self.user,
        )

        mock_create_client.return_value.delete_style.return_value = {
            'success': True,
            'already_deleted': False,
        }
        mock_create_client.return_value.delete_workspace.return_value = {
            'success': True,
            'already_deleted': False,
        }

        result = GeodataEngineService.delete_engine_cascade(self.engine, delete_remote=True)

        self.assertTrue(result['success'])
        self.assertEqual(result['summary']['layers_deleted'], 1)
        self.assertEqual(result['summary']['stores_deleted'], 1)
        self.assertEqual(result['summary']['workspaces_deleted'], 1)
        self.assertEqual(result['summary']['styles_deleted'], 1)
        self.assertFalse(GeodataEngine.objects.filter(pk=self.engine.pk).exists())
        self.assertFalse(Workspace.objects.filter(pk=workspace.pk).exists())
        self.assertFalse(Store.objects.filter(pk=store.pk).exists())
        mock_create_client.return_value.delete_style.assert_called_once_with(
            name='cascade_style',
            workspace='cascade-workspace',
            ignore_missing=True,
        )
        mock_create_client.return_value.delete_workspace.assert_called_once_with('cascade-workspace')

    def test_delete_engine_cascade_db_only_bypasses_workspace_delete_policy(self):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='vector',
            description='reserved workspace',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='vector_store',
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
        Layer.objects.create(
            workspace=workspace,
            store=store,
            name='vector_layer',
            title='Vector Layer',
            description='desc',
            table_name='vector_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )

        result = GeodataEngineService.delete_engine_cascade(self.engine, delete_remote=False)

        self.assertTrue(result['success'])
        self.assertFalse(GeodataEngine.objects.filter(pk=self.engine.pk).exists())


class GeodataEngineAdminIntegrationTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='engine-admin',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.engine = GeodataEngine.objects.create(
            name='Admin Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )

    def test_force_delete_confirmation_page_renders(self):
        response = self.client.get(reverse('admin:geodataengine_force_delete', args=[self.engine.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Force Delete Provider Tree')
        self.assertContains(response, self.engine.name)

    @patch('tosca_api.apps.geodata_providers.admin_views.engine.GeodataEngineService.delete_engine_cascade')
    def test_force_delete_confirmation_post_executes_service(self, mock_delete_engine_cascade):
        mock_delete_engine_cascade.return_value = {
            'success': True,
            'message': 'deleted',
            'summary': GeodataEngineService.get_dependency_counts(self.engine),
        }

        response = self.client.post(
            reverse('admin:geodataengine_force_delete', args=[self.engine.pk]),
            {'delete_remote': '0'},
        )

        self.assertEqual(response.status_code, 302)
        mock_delete_engine_cascade.assert_called_once_with(self.engine, delete_remote=False)
