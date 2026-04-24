from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from tosca_api.apps.geodata_providers.admin import DeleteAborted, WorkspaceAdmin, WorkspaceAdminForm
from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace
from tosca_api.apps.geodata_providers.services.commands.workspace_service import WorkspaceService


class WorkspaceServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='workspace-service-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Workspace Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
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

    @patch('tosca_api.apps.geodata_providers.services.commands.workspace_service.EngineClientFactory.create_client')
    def test_create_workspace_persists_after_remote_create(self, mock_create_client):
        client = mock_create_client.return_value
        client.create_workspace.return_value = {'success': True, 'verified': True, 'message': 'created'}

        result = WorkspaceService.create_workspace(
            engine=self.engine,
            name='new_workspace',
            description='desc',
            user=self.user,
        )

        self.assertTrue(result['success'])
        self.assertTrue(result['created'])
        self.assertIsNone(result['error'])
        self.assertFalse(result['already_exists'])
        self.assertFalse(result['already_deleted'])
        self.assertTrue(Workspace.objects.filter(geodata_engine=self.engine, name='new_workspace').exists())
        client.create_workspace.assert_called_once_with('new_workspace')

    def test_create_workspace_blocks_reserved_name(self):
        result = WorkspaceService.create_workspace(
            engine=self.engine,
            name='vector',
            description='desc',
            user=self.user,
        )

        self.assertFalse(result['success'])
        self.assertTrue(result['blocked'])
        self.assertFalse(result['already_exists'])
        self.assertFalse(result['already_deleted'])
        self.assertFalse(result['verified'])
        self.assertIsNone(result['resource'])
        self.assertIn('reserved', result['message'])

    def test_delete_workspace_safe_blocks_when_dependencies_exist(self):
        store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='dependent_store',
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
            workspace=self.workspace,
            store=store,
            name='dependent_layer',
            title='Dependent Layer',
            description='desc',
            table_name='dependent_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            created_by=self.user,
        )

        result = WorkspaceService.delete_workspace_safe(self.workspace)

        self.assertFalse(result['success'])
        self.assertTrue(result['blocked'])
        self.assertEqual(result['resource'], self.workspace)
        self.assertFalse(result['already_deleted'])
        self.assertIn('stores=1', result['message'])
        self.assertIn('layers=1', result['message'])
        self.assertTrue(Workspace.objects.filter(pk=self.workspace.pk).exists())

    @patch('tosca_api.apps.geodata_providers.services.commands.workspace_service.EngineClientFactory.create_client')
    def test_delete_workspace_safe_deletes_after_remote_delete(self, mock_create_client):
        mock_create_client.return_value.delete_workspace.return_value = {
            'success': True,
            'verified': True,
            'already_deleted': False,
            'message': 'deleted',
        }

        result = WorkspaceService.delete_workspace_safe(self.workspace)

        self.assertTrue(result['success'])
        self.assertIsNone(result['error'])
        self.assertFalse(result['already_exists'])
        self.assertEqual(result['resource'].name, 'demo_ws')
        self.assertFalse(Workspace.objects.filter(pk=self.workspace.pk).exists())


class WorkspaceAdminServiceIntegrationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='workspace-admin-user',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.engine = GeodataEngine.objects.create(
            name='Workspace Admin Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )
        self.site = AdminSite()
        self.request_factory = RequestFactory()

    def test_workspace_add_form_rejects_reserved_name(self):
        form = WorkspaceAdminForm(
            data={
                'geodata_engine': str(self.engine.pk),
                'name': 'vector',
                'description': 'desc',
                'created_by': str(self.user.pk),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('reserved', form.errors['name'][0])

    @patch('tosca_api.apps.geodata_providers.admin._run_workspace_sync')
    @patch('tosca_api.apps.geodata_providers.admin.WorkspaceService.create_workspace')
    def test_workspace_admin_save_model_uses_service(self, mock_create_workspace, mock_run_workspace_sync):
        request = self.request_factory.post('/admin/geodata_providers/workspace/add/')
        request.user = self.user
        model_admin = WorkspaceAdmin(Workspace, self.site)
        form = MagicMock()
        form.cleaned_data = {
            'geodata_engine': self.engine,
            'name': 'service_workspace',
            'description': 'desc',
        }
        workspace = Workspace(
            geodata_engine=self.engine,
            name='service_workspace',
            description='desc',
            created_by=self.user,
        )
        mock_create_workspace.return_value = {'success': True, 'resource': workspace}

        model_admin.save_model(request, workspace, form, change=False)

        mock_create_workspace.assert_called_once_with(
            engine=self.engine,
            name='service_workspace',
            description='desc',
            user=self.user,
        )
        mock_run_workspace_sync.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin.WorkspaceService.delete_workspace_safe')
    def test_workspace_admin_delete_model_uses_service(self, mock_delete_workspace):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_service_workspace',
            description='desc',
            created_by=self.user,
        )
        mock_delete_workspace.return_value = {'success': True, 'message': 'deleted'}
        request = self.request_factory.post('/admin/geodata_providers/workspace/')
        request.user = self.user
        model_admin = WorkspaceAdmin(Workspace, self.site)

        model_admin.delete_model(request, workspace)

        mock_delete_workspace.assert_called_once_with(workspace)

    @patch('tosca_api.apps.geodata_providers.admin.WorkspaceService.delete_workspace_safe')
    def test_workspace_admin_delete_queryset_uses_service(self, mock_delete_workspace):
        first = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_queryset_workspace_1',
            description='desc',
            created_by=self.user,
        )
        second = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_queryset_workspace_2',
            description='desc',
            created_by=self.user,
        )
        mock_delete_workspace.return_value = {'success': True, 'message': 'deleted'}
        request = self.request_factory.post('/admin/geodata_providers/workspace/')
        request.user = self.user
        model_admin = WorkspaceAdmin(Workspace, self.site)

        model_admin.delete_queryset(request, Workspace.objects.filter(pk__in=[first.pk, second.pk]))

        self.assertEqual(mock_delete_workspace.call_count, 2)

    @patch('tosca_api.apps.geodata_providers.admin.WorkspaceService.delete_workspace_safe')
    def test_workspace_admin_delete_model_raises_delete_aborted_on_failure(self, mock_delete_workspace):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='blocked_workspace',
            description='desc',
            created_by=self.user,
        )
        mock_delete_workspace.return_value = {'success': False, 'message': 'blocked'}
        request = self.request_factory.post('/admin/geodata_providers/workspace/')
        request.user = self.user
        model_admin = WorkspaceAdmin(Workspace, self.site)

        with self.assertRaises(DeleteAborted):
            model_admin.delete_model(request, workspace)
