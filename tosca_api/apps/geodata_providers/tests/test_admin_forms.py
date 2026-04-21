import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied
from django.http import QueryDict
from django.test import RequestFactory
from django.test import TestCase

from tosca_api.apps.geodata_providers.admin import LayerAdmin, StoreAdmin, StoreAdminForm, WorkspaceAdminForm
from tosca_api.apps.geodata_providers.geoserver.client import GeoServerClient
from tosca_api.apps.geodata_providers.admin_views.layer import tables_for_store_view
from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace


class AdminFormCreateFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='adminforms',
            email='adminforms@example.com',
            password='testpass123',
        )
        self.engine = GeodataEngine.objects.create(
            name='Admin Form Engine',
            description='test',
            base_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            created_by=self.user,
        )
        self.site = AdminSite()
        self.request_factory = RequestFactory()

    @patch('tosca_api.apps.geodata_providers.admin.EngineClientFactory.create_client')
    def test_workspace_add_form_runs_remote_create_for_unsaved_uuid_instance(self, mock_create_client):
        client = MagicMock()
        client.create_workspace.return_value = {'success': True, 'created': True}
        client.post_verify_workspace.return_value = {'verified': True, 'success': True}
        mock_create_client.return_value = client

        form = WorkspaceAdminForm(
            data={
                'geodata_engine': str(self.engine.pk),
                'name': 'new_workspace',
                'description': 'desc',
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        client.create_workspace.assert_called_once_with('new_workspace')
        client.post_verify_workspace.assert_called_once_with('new_workspace', expected_exists=True)

    @patch('tosca_api.apps.geodata_providers.admin.EngineClientFactory.create_client')
    def test_store_add_form_runs_remote_create_for_unsaved_uuid_instance(self, mock_create_client):
        client = MagicMock()
        client.create_postgis_store.return_value = {'success': True}
        client.post_verify_store.return_value = {'verified': True, 'success': True}
        mock_create_client.return_value = client

        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='store_ws',
            description='desc',
            created_by=self.user,
        )

        form = StoreAdminForm(
            data={
                'workspace': str(workspace.pk),
                'geodata_engine': '',
                'name': 'new_store',
                'store_type': 'postgis',
                'description': 'desc',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'username': 'postgres',
                'password': 'secret',
                'schema': 'public',
                'file_path': '',
                'charset': 'UTF-8',
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        client.create_postgis_store.assert_called_once_with(
            name='new_store',
            workspace='store_ws',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
        )
        client.post_verify_store.assert_called_once_with(
            'store_ws',
            'new_store',
            expected_exists=True,
            expected_details={
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'username': 'postgres',
                'schema': 'public',
            },
        )

    @patch('tosca_api.apps.geodata_providers.admin._run_workspace_sync')
    def test_store_admin_save_model_sets_engine_from_workspace(self, mock_run_workspace_sync):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='save_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store(
            workspace=workspace,
            name='store_to_save',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        request = self.request_factory.post('/admin/geodata_providers/store/add/')
        request.user = self.user
        model_admin = StoreAdmin(Store, self.site)
        form = MagicMock()
        form.cleaned_data = {'password': 'secret'}

        model_admin.save_model(request, store, form, change=False)

        store.refresh_from_db()
        self.assertEqual(store.geodata_engine, self.engine)
        mock_run_workspace_sync.assert_called_once()

    def test_store_admin_delete_is_blocked_when_layers_exist(self):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='store_with_layer',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=workspace,
            store=store,
            name='dependent_layer',
            title='Dependent Layer',
            table_name='dependent_table',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            created_by=self.user,
        )
        request = self.request_factory.post('/admin/geodata_providers/store/')
        request.user = self.user
        model_admin = StoreAdmin(Store, self.site)

        with self.assertRaises(PermissionDenied):
            model_admin.delete_model(request, store)

    @patch('tosca_api.apps.geodata_providers.admin.EngineClientFactory.create_client')
    def test_store_admin_uses_geoserver_probe_for_access_badge(self, mock_create_client):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='probe_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='probe_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        client = MagicMock()
        client.probe_store_access.return_value = {
            'success': True,
            'status': 'usable',
            'featuretype_count': 2,
        }
        mock_create_client.return_value = client
        model_admin = StoreAdmin(Store, self.site)

        badge = model_admin.geoserver_access_badge(store)

        client.probe_store_access.assert_called_once_with('probe_ws', 'probe_store')
        self.assertIn('GeoServer OK', badge)


class LayerAdminAjaxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='layeradmin',
            email='layeradmin@example.com',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.engine = GeodataEngine.objects.create(
            name='Layer Engine',
            description='test',
            base_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='layer_ws',
            description='desc',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='layer_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        self.request_factory = RequestFactory()

    def _build_request(self):
        request = self.request_factory.get('/admin/geodata_providers/layer/tables-for-store/')
        request.user = self.user
        request.GET = QueryDict(
            f'store_id={self.store.pk}&workspace_id={self.workspace.pk}'
        )
        return request

    @patch('tosca_api.apps.geodata_providers.admin_views.layer.EngineClientFactory.create_client')
    def test_tables_for_store_returns_geoserver_available_without_local_password(self, mock_create_client):
        client = MagicMock()
        client.get_available_featuretypes.return_value = ['roads', 'buildings']
        mock_create_client.return_value = client

        response = tables_for_store_view(self._build_request())

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['tables'], [])
        self.assertEqual(
            payload['geoserver_available'],
            [{'table_name': 'roads'}, {'table_name': 'buildings'}],
        )
        self.assertIn('Set connection credentials first', payload['warning'])

    @patch('tosca_api.apps.geodata_providers.admin_views.layer.get_geometry_tables')
    @patch('tosca_api.apps.geodata_providers.admin_views.layer.EngineClientFactory.create_client')
    @patch.object(Store, 'decrypted_password', new=property(lambda self: 'secret'))
    def test_tables_for_store_merges_geoserver_only_items(self, mock_create_client, mock_get_geometry_tables):
        client = MagicMock()
        client.get_available_featuretypes.return_value = ['roads', 'buildings']
        mock_create_client.return_value = client
        mock_get_geometry_tables.return_value = [
            {
                'table_name': 'roads',
                'geometry_column': 'geom',
                'geometry_type': 'MultiLineString',
                'srid': 3857,
            }
        ]

        response = tables_for_store_view(self._build_request())

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(len(payload['tables']), 1)
        self.assertEqual(payload['geoserver_available'], [{'table_name': 'buildings'}])


class LayerDeleteBehaviorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='layerdelete',
            email='layerdelete@example.com',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.engine = GeodataEngine.objects.create(
            name='Delete Engine',
            description='test',
            base_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_ws',
            description='desc',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='delete_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        self.layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='apotheken',
            title='Apotheken',
            table_name='apotheken',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        self.site = AdminSite()
        self.request_factory = RequestFactory()

    @patch('tosca_api.apps.geodata_providers.admin.EngineClientFactory.create_client')
    def test_layer_delete_model_allows_remote_already_deleted(self, mock_create_client):
        client = MagicMock()
        client.delete_layer.return_value = {
            'success': True,
            'already_deleted': True,
            'message': "Layer 'apotheken' was already absent in GeoServer.",
        }
        client.verify_featuretype.return_value = False
        mock_create_client.return_value = client

        request = self.request_factory.post(f'/admin/geodata_providers/layer/{self.layer.pk}/delete/')
        request.user = self.user
        model_admin = LayerAdmin(Layer, self.site)

        model_admin.delete_model(request, self.layer)

        self.assertFalse(Layer.objects.filter(pk=self.layer.pk).exists())


class GeoServerClientDeleteLayerTests(TestCase):
    def test_delete_layer_treats_404_as_already_deleted(self):
        client = GeoServerClient.__new__(GeoServerClient)
        client._client = MagicMock()

        class NotFoundError(Exception):
            status = 404

        client._client.delete_layer.side_effect = NotFoundError('missing')

        result = client.delete_layer('demo', 'roads')

        self.assertTrue(result['success'])
        self.assertTrue(result['already_deleted'])

    def test_publish_featuretype_uses_layer_name_and_native_table_name(self):
        client = GeoServerClient.__new__(GeoServerClient)
        client.url = 'http://example.com/geoserver'
        client._client = MagicMock()
        client._client._requests.return_value = MagicMock(status_code=201, text='created')
        client._client.edit_featuretype.return_value = 200

        result = client.publish_featuretype(
            store_name='demo_store',
            workspace='demo_ws',
            pg_table='apotheken',
            layer_name='apotheken_v2',
            title='Apotheken V2',
            srid=4326,
        )

        self.assertTrue(result['success'])
        request_call = client._client._requests.call_args
        self.assertEqual(request_call.args[0], 'post')
        self.assertIn('/rest/workspaces/demo_ws/datastores/demo_store/featuretypes', request_call.args[1])
        payload = request_call.kwargs['data']
        self.assertIn('<name>apotheken_v2</name>', payload)
        self.assertIn('<nativeName>apotheken</nativeName>', payload)
        self.assertIn('<title>Apotheken V2</title>', payload)
