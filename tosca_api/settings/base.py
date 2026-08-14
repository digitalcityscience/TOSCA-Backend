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
    DJANGO_CORS_ALLOW_CREDENTIALS=(bool, True),
)

if ENV_FILE.exists():
    env.read_env(os.fspath(ENV_FILE))
elif FALLBACK_ENV_FILE.exists():
    # Backward-compatible: allow single .env if .env.dev/.env.prod not created yet
    env.read_env(os.fspath(FALLBACK_ENV_FILE))
else:
    # Environment variables may already be injected by Docker/Kubernetes runtime.
    # In that case, proceed without reading a local .env file.
    pass

# -------------------------------------------------
# Core
# -------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="change-me-in-production")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
CORS_ALLOWED_ORIGINS = env.list("DJANGO_CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = env.bool("DJANGO_CORS_ALLOW_CREDENTIALS")
CORS_ALLOWED_METHODS = ["DELETE", "GET", "OPTIONS", "PATCH", "POST", "PUT"]
CORS_ALLOWED_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "origin",
    "x-csrftoken",
    "x-requested-with",
]
CORS_PREFLIGHT_MAX_AGE = 86400

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
    "rest_framework_gis",
    "drf_spectacular",
    "rest_framework.authtoken",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    "dj_rest_auth",
    "dj_rest_auth.registration",
    "formbuilder",
    # Local apps
    "tosca_api.apps.core",
    "tosca_api.apps.organizations",
    "tosca_api.apps.tosca_web",
    "tosca_api.apps.catalog_api.apps.CatalogApiConfig",
    "tosca_api.apps.geodata_providers.apps.GeodataProvidersConfig",
    "tosca_api.apps.campaigns",
    "tosca_api.apps.geocontext",
    "tosca_api.apps.geostories",
    "tosca_api.apps.featurelinks",
    "tosca_api.apps.events",
    "tosca_api.apps.feedback",
]

# django-basic-form-builder: enable read-only API endpoint
FORMBUILDER_API_ENABLED = True

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "tosca_api.middleware.CorsMiddleware",
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

# CONN_MAX_AGE: reuse a connection across requests for up to this many
# seconds instead of opening a fresh Postgres connection per request.
# CONN_HEALTH_CHECKS pings a reused connection before use so a connection
# that died server-side (e.g. Postgres restart) doesn't surface as a
# request-time error.
DB_CONN_MAX_AGE = env.int("DB_CONN_MAX_AGE", default=60)

# statement_timeout (ms): a stuck/runaway query is killed by Postgres
# instead of holding a worker (and a DB connection) hostage indefinitely.
DB_STATEMENT_TIMEOUT_MS = env.int("DB_STATEMENT_TIMEOUT_MS", default=30000)

DATABASES = {
        "default": {
            "ENGINE": "django.contrib.gis.db.backends.postgis",
            "NAME": env("PG_DATABASE"),
            "USER": env("PG_API_USER"),
            "PASSWORD": env("PG_API_PASSWORD"),
            "HOST": env("PG_HOST"),
            "PORT": env("PG_DOCKER_PORT"),
            "CONN_MAX_AGE": DB_CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                # schema search_path + a hard statement timeout for every
                # connection Django opens.
                "options": (
                    f"-c search_path={env('PG_SCHEMA_API', default='public')},public "
                    f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}"
                )
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

STATICFILES_DIRS = [ROOT_DIR / "static"]
STATIC_URL = env("DJANGO_STATIC_URL", default="/static/")
STATIC_ROOT = Path(env("DJANGO_STATIC_ROOT", default=os.fspath(ROOT_DIR / "staticfiles")))
MEDIA_URL = env("DJANGO_MEDIA_URL", default="/media/")
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", default=os.fspath(ROOT_DIR / "media")))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INTERNAL_API_BASE_URL = env(
    "INTERNAL_API_BASE_URL",
    default="http://localhost:8000/api/v1/providers/provider",
)

# Default PostGIS schema for GeoServer stores (task 3.5)
GIS_SCHEMA = env("PG_SCHEMA_GIS", default="public")

# Fernet key for encrypting GeoServer credentials in the DB.
# Generate once:  uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Required in every environment, including local dev — there is no default
# and this deliberately fails fast at startup if unset. Changing an existing
# key invalidates all stored credentials, so treat it like any other secret.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "tosca_api.apps.authentication.backends.KeycloakTokenAuthentication",  # JWT token auth
        "rest_framework.authentication.TokenAuthentication",  # DRF token for internal API calls
        "rest_framework.authentication.SessionAuthentication",  # Browser session
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "tosca_api.apps.core.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "TOSCA API",
    "DESCRIPTION": "Swagger/OpenAPI documentation for TOSCA Django REST endpoints.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Add common error responses to all endpoints
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
    },
    "SECURITY": [{"bearerAuth": []}],
    # Postprocessing hooks to add common responses
    "POSTPROCESSING_HOOKS": [
        "tosca_api.apps.core.schema.add_common_responses",
    ],
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
KEYCLOAK_SERVER_URL = env("KEYCLOAK_SERVER_URL", default="https://auth2.dcs.hcu-hamburg.de/")
KEYCLOAK_REALM = env("KEYCLOAK_REALM", default="tosca-dev")
KEYCLOAK_CLIENT_ID = env("KEYCLOAK_CLIENT_ID", default="django-dev")
KEYCLOAK_CLIENT_SECRET = env("KEYCLOAK_CLIENT_SECRET", default="")
KEYCLOAK_DJANGO_STAFF_ROLES = env.list(
    # ADMIN is deliberately excluded -- it's the GeoServer console escape
    # valve, not a Django role (canonical §2 "Çakışma çözümü": Django staff
    # keys off DJANGO_STAFF, not ADMIN; DJANGO_SUPERADMIN already implies
    # staff via sync_user_permissions_from_roles).
    "KEYCLOAK_DJANGO_STAFF_ROLES",
    default=["DJANGO_STAFF", "DJANGO_SUPERADMIN"],
)
KEYCLOAK_DJANGO_SUPERUSER_ROLES = env.list(
    "KEYCLOAK_DJANGO_SUPERUSER_ROLES",
    default=["DJANGO_SUPERADMIN"],
)

# JWKS / issuer used to verify access/id tokens
KEYCLOAK_JWKS_URL = env("KEYCLOAK_JWKS_URL", default=f"{KEYCLOAK_SERVER_URL}realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs")
KEYCLOAK_ISSUER = env("KEYCLOAK_ISSUER", default=f"{KEYCLOAK_SERVER_URL}realms/{KEYCLOAK_REALM}")

# Allow tokens from multiple clients (geoserver, tosca-web, mobile-app)
ALLOWED_TOKEN_AUDIENCES = env.list(
    "ALLOWED_TOKEN_AUDIENCES",
    default=["django-dev", "geoserver", "account"],
)
ALLOWED_TOKEN_CLIENTS = env.list(
    "ALLOWED_TOKEN_CLIENTS",
    default=["django-dev", "geoserver", "tosca-web"],
)

SOCIALACCOUNT_PROVIDERS = {
    "openid_connect": {
        "APPS": [
            {
                "provider_id": "keycloak",
                "name": "Keycloak",
                "client_id": KEYCLOAK_CLIENT_ID,
                "secret": KEYCLOAK_CLIENT_SECRET,
                "settings": {
                    "server_url": f"{KEYCLOAK_SERVER_URL}realms/{KEYCLOAK_REALM}/.well-known/openid-configuration",
                    # get_pkce_params() reads app.settings, i.e. this nested dict —
                    # a top-level "oauth_pkce_enabled" key on the APP entry is ignored.
                    "oauth_pkce_enabled": True,
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


# -------------------------------------------------
# GeoServer Configuration for Geodata Engine
# -------------------------------------------------
GEOSERVER_HOST = env("GEOSERVER_HOST", default="localhost")
GEOSERVER_PORT = env("GEOSERVER_PORT", default="8080")
GEOSERVER_PUBLIC_URL = env("GEOSERVER_PUBLIC_URL", default="")
GEOSERVER_ADMIN_USER = env("GEOSERVER_ADMIN_USER", default="admin2")
GEOSERVER_ADMIN_PASSWORD = env("GEOSERVER_ADMIN_PASSWORD", default="geoserver2")
