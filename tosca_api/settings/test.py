"""Settings for running automated tests."""
from .base import *  # noqa: F401,F403

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
        "TEST": {
            "NAME": "test_tosca",  # Use the same name (--reuse-db)
        },
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
