from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from tosca_api.middleware import CorsMiddleware


class CorsMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(
        CORS_ALLOWED_ORIGINS=["http://localhost:5173"],
        CORS_ALLOW_CREDENTIALS=True,
        CORS_ALLOWED_METHODS=["GET", "OPTIONS"],
        CORS_ALLOWED_HEADERS=["authorization", "content-type"],
        CORS_PREFLIGHT_MAX_AGE=600,
    )
    def test_allowed_preflight_returns_cors_headers(self):
        request = self.factory.options(
            "/api/v1/catalog/workspaces",
            HTTP_ORIGIN="http://localhost:5173",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization",
        )

        response = CorsMiddleware(lambda _: JsonResponse({"ok": True}))(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:5173")
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
        self.assertEqual(response["Access-Control-Allow-Methods"], "GET, OPTIONS")
        self.assertEqual(response["Access-Control-Allow-Headers"], "authorization, content-type")
        self.assertEqual(response["Access-Control-Max-Age"], "600")
        self.assertIn("Origin", response["Vary"])

    @override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:5173"])
    def test_disallowed_origin_gets_no_cors_headers(self):
        request = self.factory.get(
            "/api/v1/catalog/workspaces",
            HTTP_ORIGIN="http://malicious.example",
        )

        response = CorsMiddleware(lambda _: JsonResponse({"ok": True}))(request)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)
