from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from tosca_api.apps.geodata_providers.api.views import LayerViewSet
from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace

# NOTE: tosca_api/apps/geodata_providers/api/urls.py is not currently included
# in tosca_api/urls.py, so this endpoint has no resolvable URL yet. The view
# is invoked directly via APIRequestFactory instead of reverse()/self.client
# until routing is addressed (tracked separately from this fix).


class LayerPreviewEndpointTests(TestCase):
    """Regression test for LayerViewSet.preview() (missing `os` import bug)."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin_user = User.objects.create_user(
            username='preview_admin', password='x', is_staff=True, is_superuser=True,
        )
        self.view = LayerViewSet.as_view({'post': 'preview'})

    def _post(self, data):
        request = self.factory.post('/layers/preview/', data, format='json')
        force_authenticate(request, user=self.admin_user)
        return self.view(request)

    def test_preview_returns_detected_file_metadata(self):
        response = self._post({'file_name': 'roads.geojson'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])
        preview = response.data['preview']
        self.assertEqual(preview['file_name'], 'roads.geojson')
        self.assertEqual(preview['detected_type'], 'geojson')
        self.assertTrue(preview['supported'])

    def test_preview_handles_unknown_extension(self):
        response = self._post({'file_name': 'notes.txt'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['preview']['detected_type'], 'txt')
        self.assertFalse(response.data['preview']['supported'])

    def test_preview_handles_missing_file_name(self):
        response = self._post({})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['preview']['detected_type'], 'unknown')


class LayerUpdateEndpointTests(TestCase):
    """Regression tests for LayerViewSet.update() (issue 45: thin the view,
    push the allowed-fields/publish-sync logic into LayerUpdateService).
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(
            username='layer_update_user', password='x', is_staff=True, is_superuser=True,
        )
        self.engine = GeodataEngine.objects.create(
            name='Update Endpoint Engine',
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
            name='demo_store',
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
        self.layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='roads',
            title='Old Title',
            description='old',
            table_name='roads',
            geometry_column='geom',
            geometry_type='LineString',
            srid=4326,
            publishing_state='DRAFT',
            created_by=self.user,
        )
        self.view = LayerViewSet.as_view({'patch': 'update'})

    def _patch(self, data):
        request = self.factory.patch(f'/layers/{self.layer.pk}/', data, format='json')
        force_authenticate(request, user=self.user)
        return self.view(request, pk=str(self.layer.pk))

    def test_update_rejects_request_with_no_editable_fields(self):
        response = self._patch({'name': 'renamed'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('detail', response.data)

    def test_update_applies_allowed_fields_and_ignores_the_rest(self):
        response = self._patch({'title': 'New Title', 'srid': 3857, 'name': 'renamed'})

        self.assertEqual(response.status_code, 200)
        self.layer.refresh_from_db()
        self.assertEqual(self.layer.title, 'New Title')
        self.assertEqual(self.layer.srid, 3857)
        self.assertEqual(self.layer.name, 'roads')
