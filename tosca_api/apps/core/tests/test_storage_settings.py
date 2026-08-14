from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from tosca_api.settings.base import build_storage_config


class StorageConfigTests(SimpleTestCase):
    def test_filesystem_is_the_default_shape_for_local_development(self):
        config = build_storage_config("filesystem")

        self.assertEqual(
            config["default"]["BACKEND"],
            "django.core.files.storage.FileSystemStorage",
        )
        self.assertEqual(
            config["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )

    def test_s3_config_is_provider_neutral_and_keeps_staticfiles_local(self):
        config = build_storage_config(
            "s3",
            bucket_name="tosca-media",
            endpoint_url="https://garage.example.org",
            region_name="garage",
            access_key="access",
            secret_key="secret",
            addressing_style="path",
            signature_version="s3v4",
            location="media/originals/",
        )

        self.assertEqual(config["default"]["BACKEND"], "storages.backends.s3.S3Storage")
        self.assertEqual(config["default"]["OPTIONS"]["bucket_name"], "tosca-media")
        self.assertEqual(config["default"]["OPTIONS"]["endpoint_url"], "https://garage.example.org")
        self.assertEqual(config["default"]["OPTIONS"]["addressing_style"], "path")
        self.assertEqual(config["default"]["OPTIONS"]["location"], "media/originals")
        self.assertEqual(
            config["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )

    def test_s3_requires_a_bucket_name(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "S3_BUCKET_NAME is required"):
            build_storage_config("s3")
