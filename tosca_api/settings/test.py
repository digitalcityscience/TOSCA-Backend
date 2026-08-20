"""Settings for running automated tests."""
from .base import *  # noqa: F401,F403

# Automated tests must not write to the live dev Garage buckets. If the active
# environment uses S3, tests keep exercising S3 semantics but route writes to
# dedicated test buckets. If the active environment uses filesystem storage,
# tests use an isolated local media root.
MEDIA_ROOT = Path(env("DJANGO_TEST_MEDIA_ROOT", default="/tmp/tosca-test-media"))  # noqa: F405
if DJANGO_STORAGE_BACKEND == "s3":  # noqa: F405
    STORAGES = build_storage_config(  # noqa: F405
        "s3",
        bucket_name=env("S3_TEST_BUCKET_NAME", default="tosca-media-test-private"),
        public_bucket_name=env(
            "S3_TEST_PUBLIC_BUCKET_NAME", default="tosca-media-test-public"
        ),
        archive_bucket_name=env(
            "S3_TEST_ARCHIVE_BUCKET_NAME", default="tosca-media-test-archive"
        ),
        endpoint_url=S3_ENDPOINT_URL,  # noqa: F405
        region_name=S3_REGION_NAME,  # noqa: F405
        access_key=S3_ACCESS_KEY_ID,  # noqa: F405
        secret_key=S3_SECRET_ACCESS_KEY,  # noqa: F405
        addressing_style=S3_ADDRESSING_STYLE,  # noqa: F405
        signature_version=S3_SIGNATURE_VERSION,  # noqa: F405
        location=MEDIA_PRIVATE_PREFIX,  # noqa: F405
    )
else:
    STORAGES = build_storage_config("filesystem")  # noqa: F405

# Use pre-created PostGIS test database
# The test_tosca database must exist with PostGIS extension enabled.
# Create it with: CREATE DATABASE test_tosca; \c test_tosca; CREATE EXTENSION postgis;
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": "test_tosca",
        "USER": env("PG_API_USER", default="tosca_api"),
        "PASSWORD": env("PG_API_PASSWORD", default="postgres_api"),
        "HOST": env("PG_HOST", default="db"),
        "PORT": "5432",  # Internal Docker port
        "OPTIONS": {
            "options": f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}",  # noqa: F405
        },
        "TEST": {
            "NAME": "test_tosca",  # Use the same name (--reuse-db)
        },
    }
}

# Fast hashing for tests
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Test email backend
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Test-specific settings
CELERY_TASK_ALWAYS_EAGER = True
DEBUG = False
ALLOWED_HOSTS = ['testserver']  # Allow Django test client
