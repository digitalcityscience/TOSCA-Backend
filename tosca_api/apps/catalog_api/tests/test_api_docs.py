from django.test import SimpleTestCase
from django.urls import reverse


class ApiDocsUrlTestCase(SimpleTestCase):
    def test_schema_endpoint_is_registered(self):
        self.assertEqual(reverse("api-schema"), "/api/schema/")

    def test_swagger_ui_endpoint_is_registered(self):
        self.assertEqual(reverse("api-swagger-ui"), "/api/docs/")
