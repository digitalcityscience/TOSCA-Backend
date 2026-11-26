from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class VersionEndpointTests(SimpleTestCase):
    def test_reports_the_build_baked_into_the_image(self):
        env = {
            "TOSCA_BACKEND_VERSION": "1.2.3",
            "TOSCA_BACKEND_REVISION": "0123456789abcdef",
            "TOSCA_VERSION": "2026.12.1",
        }
        with patch.dict("os.environ", env):
            response = self.client.get("/api/version")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "component": "tosca-backend",
                "version": "1.2.3",
                "revision": "0123456789abcdef",
                "platform_version": "2026.12.1",
            },
        )

    def test_defaults_for_a_build_without_version_arguments(self):
        with patch.dict("os.environ", {}, clear=False) as env:
            for name in ("TOSCA_BACKEND_VERSION", "TOSCA_BACKEND_REVISION", "TOSCA_VERSION"):
                env.pop(name, None)
            response = self.client.get(reverse("version"))

        body = response.json()
        self.assertEqual((body["version"], body["revision"], body["platform_version"]), ("dev", "unknown", ""))

    def test_needs_no_sign_in_and_only_reads(self):
        self.assertEqual(self.client.get("/api/version").status_code, 200)
        self.assertEqual(self.client.post("/api/version").status_code, 405)
