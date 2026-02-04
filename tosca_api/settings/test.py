"""Settings for running automated tests."""
from .base import *  # noqa: F401,F403

# Use SQLite for tests to avoid PostgreSQL dependency
# NOTE: For PostGIS tests, use --ds=tosca_api.settings.base to run against the dev database.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
