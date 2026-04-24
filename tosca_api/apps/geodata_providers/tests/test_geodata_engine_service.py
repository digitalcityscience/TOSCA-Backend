from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Style, Workspace
from tosca_api.apps.geodata_providers.services.commands.geodata_engine_service import GeodataEngineService


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


class GeodataEngineApiServiceIntegrationTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='api-user', password='testpass123')
        self.client.force_authenticate(self.user)
        self.engine = GeodataEngine.objects.create(
            name='API Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )

    @patch('tosca_api.apps.geodata_providers.services.commands.geodata_engine_service.EngineClientFactory.create_sync_service')
    @patch('tosca_api.apps.geodata_providers.services.commands.geodata_engine_service.EngineClientFactory.create_client')
    def test_create_endpoint_uses_service_flow(self, mock_create_client, mock_create_sync_service):
        mock_create_client.return_value.validate_connection.return_value = {'success': True, 'version': '2.25'}
        mock_create_sync_service.return_value.sync_all_resources.return_value = {'success': True}

        response = self.client.post(
            '/api/geoengine/engines/',
            {
                'name': 'Posted Engine',
                'description': 'created via api',
                'engine_type': 'geoserver',
                'base_url': 'http://posted.example/geoserver',
                'admin_username': 'admin',
                'admin_password': 'secret',
                'is_active': True,
                'is_default': False,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['engine']['name'], 'Posted Engine')
        self.assertTrue(response.data['initial_sync']['success'])
        self.assertTrue(GeodataEngine.objects.filter(name='Posted Engine').exists())

    @patch('tosca_api.apps.geodata_providers.services.commands.geodata_engine_service.EngineClientFactory.create_sync_service')
    @patch('tosca_api.apps.geodata_providers.services.commands.geodata_engine_service.EngineClientFactory.create_client')
    def test_partial_update_keeps_existing_password_when_not_sent(self, mock_create_client, mock_create_sync_service):
        mock_create_client.return_value.validate_connection.return_value = {'success': True, 'version': '2.25'}
        mock_create_sync_service.return_value.sync_all_resources.return_value = {'success': True}

        response = self.client.patch(
            f'/api/geoengine/engines/{self.engine.pk}/',
            {'base_url': 'http://updated.example/geoserver'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.engine.refresh_from_db()
        self.assertEqual(self.engine.base_url, 'http://updated.example/geoserver')
        self.assertEqual(self.engine.admin_password, 'secret')

    @patch('tosca_api.apps.geodata_providers.api.views.GeodataEngineService.validate_engine_connection')
    def test_test_connection_endpoint_uses_service(self, mock_validate):
        mock_validate.return_value = {'success': True, 'message': 'Connection validated', 'version': '2.25'}

        response = self.client.post(
            '/api/geoengine/engines/test_connection/',
            {
                'base_url': 'http://validate.example/geoserver',
                'admin_username': 'admin',
                'admin_password': 'secret',
                'engine_type': 'geoserver',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['version'], '2.25')
        mock_validate.assert_called_once()


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
