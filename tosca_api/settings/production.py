"""Settings for production deployment."""
from pathlib import Path

from .base import *  # noqa: F401,F403

# Fail fast: never boot production with the insecure development default.
SECRET_KEY = env("DJANGO_SECRET_KEY")  # noqa: F405

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [Path(__file__).resolve().parent.parent.parent / 'templates'],
    'OPTIONS': {
        'loaders': [
            ('django.template.loaders.cached.Loader', [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ]),
        ],
        'context_processors': [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ],
    },
}]

DEBUG = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Production logging: stdout/stderr JSON only, so the container runtime
# (Docker/Kubernetes) owns log collection/rotation instead of the app
# writing to /app/logs files that may not persist or get shipped anywhere.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(levelname)s %(asctime)s %(name)s %(message)s'
        },
    },
    'handlers': {
        'stdout': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
            'formatter': 'json',
        },
        'stderr': {
            'level': 'WARNING',
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stderr',
            'formatter': 'json',
        },
    },
    'root': {
        'handlers': ['stdout'],
        'level': 'WARNING',
    },
    'loggers': {
        'tosca_api': {
            'handlers': ['stdout'],
            'level': 'INFO',
            'propagate': False,
        },
        'tosca_api.apps.authentication': {
            'handlers': ['stderr'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['stderr'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['stderr'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
