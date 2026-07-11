from io import StringIO

from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import SimpleTestCase, override_settings

from tosca_api.apps.core.checks import (
    check_field_encryption_key,
    check_geoserver_admin_password,
    check_keycloak_settings,
    check_secret_key,
    check_static_and_media_roots,
)

VALID_FERNET_KEY = "dkCahk107lTNdgJsU_lqDB5BKmh83hM4rDQojKRPxPQ="


class CheckSecretKeyTests(SimpleTestCase):
    @override_settings(SECRET_KEY="change-me-in-production")
    def test_fails_on_default_secret_key(self):
        errors = check_secret_key(None)
        self.assertEqual([e.id for e in errors], ["tosca.settings.E001"])

    @override_settings(SECRET_KEY="a-real-unique-unpredictable-value")
    def test_passes_on_real_secret_key(self):
        self.assertEqual(check_secret_key(None), [])


class CheckFieldEncryptionKeyTests(SimpleTestCase):
    @override_settings(FIELD_ENCRYPTION_KEY="")
    def test_fails_when_unset(self):
        errors = check_field_encryption_key(None)
        self.assertEqual([e.id for e in errors], ["tosca.settings.E002"])

    @override_settings(FIELD_ENCRYPTION_KEY="not-a-valid-fernet-key")
    def test_fails_when_invalid(self):
        errors = check_field_encryption_key(None)
        self.assertEqual([e.id for e in errors], ["tosca.settings.E003"])

    @override_settings(FIELD_ENCRYPTION_KEY=VALID_FERNET_KEY)
    def test_passes_on_valid_key(self):
        self.assertEqual(check_field_encryption_key(None), [])


class CheckKeycloakSettingsTests(SimpleTestCase):
    @override_settings(KEYCLOAK_ISSUER="", KEYCLOAK_JWKS_URL="", KEYCLOAK_CLIENT_ID="django-dev")
    def test_fails_when_all_unset_or_default(self):
        errors = check_keycloak_settings(None)
        self.assertEqual(
            {e.id for e in errors},
            {"tosca.settings.E004", "tosca.settings.E005", "tosca.settings.E006"},
        )

    @override_settings(
        KEYCLOAK_ISSUER="https://auth.example.com/realms/prod",
        KEYCLOAK_JWKS_URL="https://auth.example.com/realms/prod/protocol/openid-connect/certs",
        KEYCLOAK_CLIENT_ID="tosca-prod",
    )
    def test_passes_when_all_configured(self):
        self.assertEqual(check_keycloak_settings(None), [])


class CheckGeoserverAdminPasswordTests(SimpleTestCase):
    @override_settings(GEOSERVER_ADMIN_PASSWORD="geoserver2")
    def test_fails_on_default_password(self):
        errors = check_geoserver_admin_password(None)
        self.assertEqual([e.id for e in errors], ["tosca.settings.E007"])

    @override_settings(GEOSERVER_ADMIN_PASSWORD="a-real-password")
    def test_passes_on_real_password(self):
        self.assertEqual(check_geoserver_admin_password(None), [])


class CheckStaticAndMediaRootsTests(SimpleTestCase):
    @override_settings(STATIC_ROOT=None, MEDIA_ROOT=None)
    def test_fails_when_unset(self):
        errors = check_static_and_media_roots(None)
        self.assertEqual(
            {e.id for e in errors},
            {"tosca.settings.E008", "tosca.settings.E009"},
        )

    def test_passes_with_default_settings(self):
        self.assertEqual(check_static_and_media_roots(None), [])


class DeployCheckCommandTests(SimpleTestCase):
    """`manage.py check --deploy` must actually fail when settings are
    unsafe, and must not be affected by these checks under normal
    (non-deploy) operation.
    """

    @override_settings(SECRET_KEY="change-me-in-production")
    def test_check_deploy_fails_on_unsafe_secret_key(self):
        with self.assertRaises(SystemCheckError) as ctx:
            call_command("check", deploy=True, stdout=StringIO(), stderr=StringIO())
        self.assertIn("tosca.settings.E001", str(ctx.exception))

    @override_settings(SECRET_KEY="change-me-in-production")
    def test_check_without_deploy_flag_ignores_unsafe_secret_key(self):
        # deploy=True-gated checks must not fire on a plain `manage.py check`.
        call_command("check", stdout=StringIO(), stderr=StringIO())
