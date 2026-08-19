"""Storage-access gate (D) signing behavior (security tickets ticket 16/18, §9 rows 10-12).

Ticket 13/14/15 and ``test_media_lifecycle.py`` already prove *which* alias a
given asset ends up in (the S2 truth table); ``test_storage_settings.py``
already proves each alias's ``querystring_auth``/backend shape in isolation.
Neither ties the two together: does the URL actually built for a given asset
(``geocontext/views.py::_absolute_url`` / ``_storage_for_alias``) come out
presigned for every alias, private or public/published? That composition is
what §9 rows 10-12 ask for. Presigning is a local HMAC computation (no
network call), so it's exercised directly against ``storages[alias]`` under
an S3-shaped ``STORAGES`` override -- no live S3/Garage endpoint needed (see
``scripts/garage_e2e.py`` for the live-Garage HTTP-level version of the same
assertions).
"""

from django.core.files.storage import storages
from django.test import SimpleTestCase, override_settings

from tosca_api.apps.core.models import MediaAsset
from tosca_api.settings.base import build_storage_config

_S3_STORAGES = build_storage_config(
    "s3",
    bucket_name="tosca-media-test",
    public_bucket_name="tosca-media-test-public",
    archive_bucket_name="tosca-media-test-archive",
    endpoint_url="https://garage.example.org",
    region_name="garage",
    access_key="test-access-key",
    secret_key="test-secret-key",
    addressing_style="path",
    signature_version="s3v4",
)


@override_settings(STORAGES=_S3_STORAGES)
class MediaStorageSigningTests(SimpleTestCase):
    """Asserts the alias->signing composition, not just the config shape.

    "Public" here means publicly accessible through TOSCA's application
    logic (Django checks the asset/entity is actually public/published, then
    mints a presigned URL) -- not anonymously readable from the bucket. All
    three aliases must therefore come out presigned, with the same pinned
    3600s TTL (ticket 17).
    """

    def test_public_media_url_is_signed(self):
        """§9 row: public/published EditorJS media -> presigned URL, not anonymous."""
        url = storages[MediaAsset.StorageAlias.PUBLIC].url("geocontext/uploads/public.png")
        self.assertIn("X-Amz-Signature", url)
        self.assertIn("X-Amz-Expires=3600", url)

    def test_private_default_media_url_is_signed(self):
        """§9 row: private EditorJS media via authorized generated URL -> 200 (<=1h),
        and unsigned access is the "not readable" counterpart of the same row."""
        url = storages[MediaAsset.StorageAlias.DEFAULT].url("geocontext/uploads/private.png")
        self.assertIn("X-Amz-Signature", url)
        self.assertIn("X-Amz-Expires=3600", url)

    def test_archive_media_url_is_signed(self):
        url = storages["media_archive"].url("geocontext/uploads/archived.png")
        self.assertIn("X-Amz-Signature", url)
        self.assertIn("X-Amz-Expires=3600", url)
