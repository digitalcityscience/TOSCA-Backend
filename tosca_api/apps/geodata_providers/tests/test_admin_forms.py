import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied
from django.http import QueryDict
from django.test import RequestFactory
from django.test import TestCase

from tosca_api.apps.geodata_providers.admin import DeleteAborted, LayerAdmin, StoreAdmin, StoreAdminForm, WorkspaceAdminForm
from tosca_api.apps.geodata_providers.admin_actions.layer import publish_layer, unpublish_layer
from tosca_api.apps.geodata_providers.admin_views.layer import publish_postgis_view
from tosca_api.apps.geodata_providers.admin_views.store import store_clone_view
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
                'created_by': str(self.user.pk),
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

        with self.assertRaises(DeleteAborted):
            model_admin.delete_model(request, store)

    @patch('tosca_api.apps.geodata_providers.admin.StoreService.delete_store_safe')
    def test_store_admin_delete_model_uses_service(self, mock_delete_store_safe):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_service_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='delete_service_store',
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
        mock_delete_store_safe.return_value = {'success': True, 'message': 'deleted'}
        request = self.request_factory.post('/admin/geodata_providers/store/')
        request.user = self.user
        model_admin = StoreAdmin(Store, self.site)

        model_admin.delete_model(request, store)

        mock_delete_store_safe.assert_called_once_with(store)

    @patch('tosca_api.apps.geodata_providers.admin.StoreService.delete_store_safe')
    def test_store_admin_delete_queryset_uses_service(self, mock_delete_store_safe):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_queryset_ws',
            description='desc',
            created_by=self.user,
        )
        first = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='delete_queryset_store_1',
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
        second = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='delete_queryset_store_2',
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
        mock_delete_store_safe.return_value = {'success': True, 'message': 'deleted'}
        request = self.request_factory.post('/admin/geodata_providers/store/')
        request.user = self.user
        model_admin = StoreAdmin(Store, self.site)

        model_admin.delete_queryset(request, Store.objects.filter(pk__in=[first.pk, second.pk]))

        self.assertEqual(mock_delete_store_safe.call_count, 2)

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

    @patch('tosca_api.apps.geodata_providers.admin_views.store.messages.warning')
    @patch('tosca_api.apps.geodata_providers.admin_views.store.messages.success')
    @patch('tosca_api.apps.geodata_providers.admin_views.store.StoreService.clone_store')
    def test_store_clone_view_uses_service(self, mock_clone_store, mock_success, mock_warning):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='clone_ws',
            description='desc',
            created_by=self.user,
        )
        source = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='source_clone_store',
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
        cloned = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='cloned_store_result',
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
        mock_clone_store.return_value = {
            'success': True,
            'resource': cloned,
            'sync_result': {'success': True, 'errors': []},
        }
        request = self.request_factory.post(
            f'/admin/geodata_providers/store/{source.pk}/clone/',
            data={
                'name': 'cloned_store',
                'workspace': str(workspace.pk),
                'description': 'desc',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'schema': 'public',
                'username': 'postgres',
                'password': 'secret',
            },
        )
        request.user = User.objects.create_user(
            username='clone-admin',
            email='clone@example.com',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )

        response = store_clone_view(request, source.pk)

        self.assertEqual(response.status_code, 302)
        mock_clone_store.assert_called_once()
        mock_success.assert_called()
        mock_warning.assert_not_called()


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

    @patch('tosca_api.apps.geodata_providers.admin.LayerService.delete_layer_safe')
    def test_layer_delete_model_allows_remote_already_deleted(self, mock_delete_layer_safe):
        mock_delete_layer_safe.return_value = {
            'success': True,
            'already_deleted': True,
            'message': "Layer 'apotheken' was already absent in GeoServer.",
        }

        request = self.request_factory.post(f'/admin/geodata_providers/layer/{self.layer.pk}/delete/')
        request.user = self.user
        model_admin = LayerAdmin(Layer, self.site)
        model_admin.message_user = MagicMock()

        model_admin.delete_model(request, self.layer)

        mock_delete_layer_safe.assert_called_once_with(self.layer)
        model_admin.message_user.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin.LayerService.delete_layer_safe')
    def test_layer_delete_queryset_uses_service_for_each_object(self, mock_delete_layer_safe):
        second_layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='hastane',
            title='Hastane',
            table_name='hastane',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        mock_delete_layer_safe.return_value = {'success': True, 'message': 'deleted'}

        request = self.request_factory.post('/admin/geodata_providers/layer/')
        request.user = self.user
        model_admin = LayerAdmin(Layer, self.site)

        model_admin.delete_queryset(request, Layer.objects.filter(pk__in=[self.layer.pk, second_layer.pk]))

        self.assertEqual(mock_delete_layer_safe.call_count, 2)

    @patch('tosca_api.apps.geodata_providers.admin._run_workspace_sync')
    @patch('tosca_api.apps.geodata_providers.admin.LayerService.update_published_metadata')
    def test_layer_save_model_uses_service_for_published_metadata_only_changes(self, mock_update_metadata, mock_run_workspace_sync):
        request = self.request_factory.post(f'/admin/geodata_providers/layer/{self.layer.pk}/change/')
        request.user = self.user
        model_admin = LayerAdmin(Layer, self.site)
        self.layer.title = 'Updated Apotheken'
        form = MagicMock()
        form.changed_data = ['title']

        model_admin.save_model(request, self.layer, form, change=True)

        mock_update_metadata.assert_called_once_with(
            layer=self.layer,
            title='Updated Apotheken',
            description=self.layer.description,
        )
        mock_run_workspace_sync.assert_called_once()


class LayerSurfaceRefactorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='layersurface',
            email='layersurface@example.com',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.engine = GeodataEngine.objects.create(
            name='Surface Engine',
            description='test',
            base_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='surface_ws',
            description='desc',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='surface_store',
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
            name='surface_layer',
            title='Surface Layer',
            description='desc',
            table_name='surface_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        self.request_factory = RequestFactory()
        self.site = AdminSite()

    @patch('tosca_api.apps.geodata_providers.admin_actions.layer.LayerService.publish_existing_layer')
    def test_publish_action_uses_layer_service(self, mock_publish_existing_layer):
        self.layer.publishing_state = 'DRAFT'
        self.layer.save(update_fields=['publishing_state'])
        mock_publish_existing_layer.return_value = {'success': True, 'message': 'published'}
        model_admin = LayerAdmin(Layer, self.site)
        model_admin.message_user = MagicMock()
        request = self.request_factory.post('/admin/geodata_providers/layer/')
        request.user = self.user

        publish_layer(model_admin, request, Layer.objects.filter(pk=self.layer.pk))

        mock_publish_existing_layer.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin_actions.layer.LayerService.unpublish_layer')
    def test_unpublish_action_uses_layer_service(self, mock_unpublish_layer):
        mock_unpublish_layer.return_value = {'success': True, 'message': 'unpublished'}
        model_admin = LayerAdmin(Layer, self.site)
        model_admin.message_user = MagicMock()
        request = self.request_factory.post('/admin/geodata_providers/layer/')
        request.user = self.user

        unpublish_layer(model_admin, request, Layer.objects.filter(pk=self.layer.pk))

        mock_unpublish_layer.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin_views.layer.messages.warning')
    @patch('tosca_api.apps.geodata_providers.admin_views.layer.messages.success')
    @patch('tosca_api.apps.geodata_providers.admin_views.layer.GeoServerSyncService')
    @patch('tosca_api.apps.geodata_providers.admin_views.layer.LayerService.publish_postgis')
    def test_publish_postgis_view_uses_layer_service(self, mock_publish_postgis, mock_sync_service, mock_success, mock_warning):
        mock_publish_postgis.return_value = {
            'success': True,
            'created': True,
            'message': 'published',
            'resource': self.layer,
        }
        mock_sync_service.return_value.sync_layers_for_workspace.return_value = {'errors': []}
        request = self.request_factory.post(
            '/admin/geodata_providers/layer/publish-postgis/',
            data={
                'workspace': str(self.workspace.pk),
                'store': str(self.store.pk),
                'table_name': 'surface_layer',
                'layer_name': 'surface_layer',
                'title': 'Surface Layer',
                'description': 'desc',
                'geometry_column': 'geom',
                'geometry_type': 'Point',
                'srid': 4326,
            },
        )
        request.user = self.user

        response = publish_postgis_view(request)

        self.assertEqual(response.status_code, 302)
        mock_publish_postgis.assert_called_once()
        mock_success.assert_called()
        mock_warning.assert_not_called()


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

    def test_verify_featuretype_metadata_detects_mismatch(self):
        client = GeoServerClient.__new__(GeoServerClient)
        client.get_featuretype_detail = MagicMock(return_value={
            'name': 'apotheken_v2',
            'title': 'Wrong Title',
            'native_name': 'apotheken',
            'abstract': '',
            'advertised': True,
        })

        result = client.verify_featuretype_metadata(
            workspace='demo_ws',
            store_name='demo_store',
            featuretype_name='apotheken_v2',
            expected_title='Apotheken V2',
            expected_abstract='Expected abstract',
        )

        self.assertFalse(result['verified'])
        self.assertIn('title', result['mismatches'])
        self.assertIn('abstract', result['mismatches'])
