"""Settings for running automated tests."""
from .base import *  # noqa: F401,F403

# Use development PostgreSQL database directly (no test DB creation)
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": os.getenv("PG_DATABASE", "tosca"),
        "USER": os.getenv("PG_SUPERUSER", "postgres"),
        "PASSWORD": os.getenv("PG_SUPERPASS", "postgres"),
        "HOST": os.getenv("PG_HOST", "db"),
        "PORT": os.getenv("PG_DOCKER_PORT", "5432"),
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
