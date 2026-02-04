"""Base settings shared across environments."""
from __future__ import annotations

import os
from pathlib import Path

import environ

# -------------------------------------------------
# Paths
# -------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BASE_DIR = ROOT_DIR / "tosca_api"

# -------------------------------------------------
# ENV selection (.env.dev / .env.prod) with fallback
# -------------------------------------------------
ENV = os.getenv("ENV", "dev")  # dev | prod
ENV_FILE = ROOT_DIR / f".env.{ENV}"
FALLBACK_ENV_FILE = ROOT_DIR / ".env"

# -------------------------------------------------
# django-environ setup
# -------------------------------------------------
env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)

if ENV_FILE.exists():
    env.read_env(os.fspath(ENV_FILE))
elif FALLBACK_ENV_FILE.exists():
    # Backward-compatible: allow single .env if .env.dev/.env.prod not created yet
    env.read_env(os.fspath(FALLBACK_ENV_FILE))
else:
    raise RuntimeError(f"Missing env file: {ENV_FILE} (and no fallback .env found)")

# -------------------------------------------------
# Core
# -------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="change-me-in-production")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.gis",  # GeoDjango for PostGIS support
    # Local apps that override third-party templates
    "tosca_api.apps.authentication",  # Override allauth templates
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    "dj_rest_auth",
    "dj_rest_auth.registration",
    # Local apps
    "tosca_api.apps.core",
    "tosca_api.apps.tosca_web",
    "tosca_api.apps.campaigns",
    "tosca_api.apps.geocontext",
    "tosca_api.apps.layerrefs",
]

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "tosca_api.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [ROOT_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "tosca_api.wsgi.application"
ASGI_APPLICATION = "tosca_api.asgi.application"

# -------------------------------------------------
# Database (parametric; NO sqlite fallback)
# Priority:
#   1) DATABASE_URL if provided
#   2) PG_* variables (PG_HOST, PG_PORT, PG_DATABASE, PG_API_USER, PG_API_PASSWORD)
# -------------------------------------------------

DATABASES = {
        "default": {
            "ENGINE": "django.contrib.gis.db.backends.postgis",
            "NAME": env("PG_DATABASE"),
            "USER": env("PG_API_USER"),
            "PASSWORD": env("PG_API_PASSWORD"),
            "HOST": env("PG_HOST"),
            "PORT": env("PG_DOCKER_PORT"),
            "OPTIONS": {
                # optional: schema search_path for Django connections
                "options": f"-c search_path={env('PG_SCHEMA_API', default='public')},public"
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = ROOT_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = ROOT_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "tosca_api.apps.authentication.backends.KeycloakTokenAuthentication",  # JWT token auth
        "rest_framework.authentication.SessionAuthentication",  # Browser session
    ],
    "DEFAULT_PAGINATION_CLASS": "tosca_api.apps.core.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
}

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",  # Django native (fallback)
    "allauth.account.auth_backends.AuthenticationBackend",  # allauth
]

SOCIALACCOUNT_ADAPTER = "tosca_api.apps.authentication.backends.KeycloakAdapter"

# -------------------------------------------------
# Allauth (Keycloak-first)
# - Fixes deprecation warnings:
#   ACCOUNT_EMAIL_REQUIRED / ACCOUNT_USERNAME_REQUIRED removed
# - Keep explicit, consistent signup/login fields
# -------------------------------------------------
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]

# Disable local signup - only Keycloak login allowed
ACCOUNT_ADAPTER = "tosca_api.apps.authentication.backends.NoSignupAccountAdapter"
SOCIALACCOUNT_ONLY = True
SOCIALACCOUNT_LOGIN_ON_GET = True
ACCOUNT_EMAIL_VERIFICATION = "none"  # Keycloak handles it
ACCOUNT_LOGOUT_ON_GET = True
SOCIALACCOUNT_AUTO_SIGNUP = True

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/welcome/"
SOCIALACCOUNT_LOGIN_REDIRECT_URL = "/welcome/"
LOGOUT_REDIRECT_URL = "/accounts/logout/"

# -------------------------------------------------
# Keycloak / OIDC
# -------------------------------------------------
KEYCLOAK_SERVER_URL = env("KEYCLOAK_SERVER_URL", default="https://auth.dcs.hcu-hamburg.de/")
KEYCLOAK_REALM = env("KEYCLOAK_REALM", default="prod-realm")
KEYCLOAK_CLIENT_ID = env("KEYCLOAK_CLIENT_ID", default="django-dev")

# JWKS / issuer used to verify access/id tokens
KEYCLOAK_JWKS_URL = env("KEYCLOAK_JWKS_URL", default=f"{KEYCLOAK_SERVER_URL}realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs")
KEYCLOAK_ISSUER = env("KEYCLOAK_ISSUER", default=f"{KEYCLOAK_SERVER_URL}realms/{KEYCLOAK_REALM}")

# Allow tokens from multiple clients (geoserver, tosca-web, mobile-app)
ALLOWED_TOKEN_AUDIENCES = ["django-dev", "geoserver", "account"]
ALLOWED_TOKEN_CLIENTS = ["django-dev", "geoserver", "tosca-web"]

SOCIALACCOUNT_PROVIDERS = {
    "openid_connect": {
        "APPS": [
            {
                "provider_id": "keycloak",
                "name": "Keycloak",
                "client_id": KEYCLOAK_CLIENT_ID,
                "secret": "",  # Public client
                "settings": {
                    "server_url": f"{KEYCLOAK_SERVER_URL}realms/{KEYCLOAK_REALM}/.well-known/openid-configuration",
                },
            }
        ]
    }
}

# -------------------------------------------------
# Logging Configuration
# -------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {name} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {name} {message}',
            'style': '{',
        },
        'security': {
            'format': '{levelname} {asctime} SECURITY {name} {message}',
            'style': '{',
        },
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(levelname)s %(asctime)s %(name)s %(process)d %(thread)d %(message)s'
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'security_console': {
            'level': 'WARNING',
            'class': 'logging.StreamHandler',
            'formatter': 'security',
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/tosca_api.log',
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,
            'formatter': 'json',
        },
        'security_file': {
            'level': 'WARNING',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/security.log',
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,
            'formatter': 'json',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'tosca_api': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'tosca_api.apps.authentication': {
            'handlers': ['security_console', 'security_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['security_console', 'security_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['file'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}