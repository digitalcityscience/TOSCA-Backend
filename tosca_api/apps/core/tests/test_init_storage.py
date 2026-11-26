"""Tests for ``manage.py init_storage``.

The unit tests replace the storages with fakes, so they run with any storage
backend. The last test talks to the real S3 service and runs only when the
test settings use S3 (as in CI, against Garage).
"""
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from django.core.files.storage import storages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

COMMAND = "tosca_api.apps.core.management.commands.init_storage"
S3_STORAGES = {
    "default": {"BACKEND": "storages.backends.s3.S3Storage"},
    "media_public": {"BACKEND": "storages.backends.s3.S3Storage"},
    "media_archive": {"BACKEND": "storages.backends.s3.S3Storage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "Op")


def fake_storage(bucket, head=None, create=None):
    storage = MagicMock()
    storage.bucket_name = bucket
    client = storage.connection.meta.client
    client.head_bucket.side_effect = head
    client.create_bucket.side_effect = create
    return storage


def run(fakes, *args):
    out = StringIO()
    with patch(f"{COMMAND}.storages", fakes), patch(f"{COMMAND}.call_command") as cors:
        call_command("init_storage", *args, stdout=out)
    return out.getvalue(), cors


@override_settings(STORAGES=S3_STORAGES, CORS_ALLOWED_ORIGINS=["https://tosca.example"])
class InitStorageTests(SimpleTestCase):
    def test_creates_missing_buckets_and_configures_cors(self):
        fakes = {
            "default": fake_storage("private", head=client_error("404")),
            "media_public": fake_storage("public", head=client_error("NoSuchBucket")),
            "media_archive": fake_storage("archive"),
        }

        out, cors = run(fakes)

        fakes["default"].connection.meta.client.create_bucket.assert_called_once_with(Bucket="private")
        fakes["media_public"].connection.meta.client.create_bucket.assert_called_once_with(Bucket="public")
        fakes["media_archive"].connection.meta.client.create_bucket.assert_not_called()
        self.assertIn("default (private): created", out)
        self.assertIn("media_archive (archive): exists", out)
        cors.assert_called_once()
        self.assertEqual(cors.call_args.args, ("configure_storage_cors",))

    def test_is_idempotent_when_everything_exists(self):
        fakes = {alias: fake_storage(alias) for alias in ("default", "media_public", "media_archive")}

        out, _ = run(fakes)

        for storage in fakes.values():
            storage.connection.meta.client.create_bucket.assert_not_called()
        self.assertEqual(out.count(": exists"), 3)

    def test_bucket_created_concurrently_by_the_same_key_is_fine(self):
        fakes = {
            "default": fake_storage(
                "private", head=client_error("404"), create=client_error("BucketAlreadyOwnedByYou")
            ),
            "media_public": fake_storage("public"),
            "media_archive": fake_storage("archive"),
        }

        out, _ = run(fakes)

        self.assertIn("default (private): created", out)

    def test_bucket_without_access_is_an_error_and_is_not_recreated(self):
        fakes = {
            "default": fake_storage("private", head=client_error("403")),
            "media_public": fake_storage("public"),
            "media_archive": fake_storage("archive"),
        }

        with self.assertRaisesMessage(CommandError, "cannot access bucket"):
            run(fakes)
        fakes["default"].connection.meta.client.create_bucket.assert_not_called()

    def test_create_failure_is_an_error(self):
        fakes = {
            "default": fake_storage("private", head=client_error("404"), create=client_error("AccessDenied")),
            "media_public": fake_storage("public"),
            "media_archive": fake_storage("archive"),
        }

        with self.assertRaisesMessage(CommandError, "cannot create bucket"):
            run(fakes)

    def test_skip_cors(self):
        fakes = {alias: fake_storage(alias) for alias in ("default", "media_public", "media_archive")}

        _, cors = run(fakes, "--skip-cors")

        cors.assert_not_called()

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_without_cors_origins_it_warns_and_skips_cors(self):
        fakes = {alias: fake_storage(alias) for alias in ("default", "media_public", "media_archive")}

        out, cors = run(fakes)

        cors.assert_not_called()
        self.assertIn("storage CORS not configured", out)

    def test_filesystem_storage_needs_nothing(self):
        fakes = {alias: MagicMock(spec=[]) for alias in ("default", "media_public", "media_archive")}

        out, cors = run(fakes)

        self.assertIn("nothing to do", out)
        cors.assert_not_called()


@pytest.mark.skipif(not hasattr(storages["default"], "bucket_name"), reason="needs S3 storage")
def test_against_real_s3_creates_buckets_and_is_idempotent():
    call_command("init_storage", "--skip-cors", stdout=StringIO())
    out = StringIO()
    call_command("init_storage", "--skip-cors", stdout=out)

    for alias in ("default", "media_public", "media_archive"):
        storage = storages[alias]
        storage.connection.meta.client.head_bucket(Bucket=storage.bucket_name)
    assert out.getvalue().count(": exists") == 3
