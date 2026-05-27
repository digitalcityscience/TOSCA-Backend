from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from tosca_api.apps.geodata_providers.models import GeodataEngine


class SetupDefaultEngineCommandTests(TestCase):
    @patch("tosca_api.apps.geodata_providers.management.commands.setup_default_engine.requests.get")
    @override_settings(
        ENV="dev",
        GEOSERVER_HOST="geoserver",
        GEOSERVER_PORT="8585",
        GEOSERVER_PUBLIC_URL="",
        GEOSERVER_ADMIN_USER="admin2",
        GEOSERVER_ADMIN_PASSWORD="geoserver2",
    )
    def test_setup_default_engine_derives_localhost_public_url_in_dev(self, get_mock):
        get_mock.return_value = MagicMock(status_code=200)
        stdout = StringIO()

        call_command("setup_default_engine", stdout=stdout)

        engine = GeodataEngine.objects.get(name="Default GeoServer")
        self.assertEqual(engine.base_url, "http://geoserver:8585/geoserver")
        self.assertEqual(engine.public_url, "http://localhost:8585/geoserver")
        self.assertTrue(engine.is_active)
        self.assertTrue(engine.is_default)
        self.assertEqual(engine.admin_username, "admin2")
        self.assertTrue(User.objects.filter(username="admin").exists())

    @override_settings(
        ENV="prod",
        GEOSERVER_HOST="geoserver",
        GEOSERVER_PORT="8080",
        GEOSERVER_PUBLIC_URL="",
        GEOSERVER_ADMIN_USER="admin",
        GEOSERVER_ADMIN_PASSWORD="secret",
    )
    def test_setup_default_engine_requires_public_url_in_prod(self):
        stdout = StringIO()

        call_command("setup_default_engine", stdout=stdout)

        self.assertIn("GEOSERVER_PUBLIC_URL is required in production", stdout.getvalue())
        self.assertFalse(GeodataEngine.objects.exists())
        self.assertFalse(User.objects.exists())

    @patch("tosca_api.apps.geodata_providers.management.commands.setup_default_engine.requests.get")
    @override_settings(
        ENV="prod",
        GEOSERVER_HOST="geoserver",
        GEOSERVER_PORT="8080",
        GEOSERVER_PUBLIC_URL="https://geoserver.example.com/geoserver",
        GEOSERVER_ADMIN_USER="admin",
        GEOSERVER_ADMIN_PASSWORD="secret",
    )
    def test_setup_default_engine_uses_configured_public_url_in_prod(self, get_mock):
        get_mock.return_value = MagicMock(status_code=200)
        stdout = StringIO()

        call_command("setup_default_engine", stdout=stdout)

        engine = GeodataEngine.objects.get(name="Default GeoServer")
        self.assertEqual(engine.base_url, "http://geoserver:8080/geoserver")
        self.assertEqual(engine.public_url, "https://geoserver.example.com/geoserver")

    @patch("tosca_api.apps.geodata_providers.management.commands.setup_default_engine.requests.get")
    @override_settings(
        ENV="dev",
        GEOSERVER_HOST="geoserver",
        GEOSERVER_PORT="8080",
        GEOSERVER_PUBLIC_URL="http://localhost:8080/geoserver",
        GEOSERVER_ADMIN_USER="admin2",
        GEOSERVER_ADMIN_PASSWORD="geoserver2",
    )
    def test_setup_default_engine_updates_existing_default_public_url(self, get_mock):
        get_mock.return_value = MagicMock(status_code=200)
        user = User.objects.create_superuser(
            username="admin",
            email="admin@local.dev",
            password="admin123",
        )
        engine = GeodataEngine.objects.create(
            name="Default GeoServer",
            description="existing provider",
            base_url="http://geoserver:8080/geoserver",
            public_url="http://geoserver:8080/geoserver",
            admin_username="admin2",
            admin_password="geoserver2",
            is_active=True,
            is_default=True,
            created_by=user,
        )
        stdout = StringIO()

        call_command("setup_default_engine", stdout=stdout)

        engine.refresh_from_db()
        self.assertEqual(engine.base_url, "http://geoserver:8080/geoserver")
        self.assertEqual(engine.public_url, "http://localhost:8080/geoserver")
        self.assertIn("Updated existing default GeodataEngine fields", stdout.getvalue())
