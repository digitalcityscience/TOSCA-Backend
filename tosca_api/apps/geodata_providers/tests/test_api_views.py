from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from tosca_api.apps.geodata_providers.api.views import LayerViewSet

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
