"""Settings for production deployment.

Production settings should prioritize security and performance. we should check below settings for  production environment.
# Django internally does this:
class CachedLoader:
    def __init__(self):
        self.template_cache = {}
    
    def get_template(self, template_name):
        # First check: Already loaded?
        if template_name in self.template_cache:
            return self.template_cache[template_name]  # ← Instant!
        
        # Only on first request:
        for app in INSTALLED_APPS:
            # Check file system once
            # Cache the result
        
        self.template_cache[template_name] = template
        return template
"""
from .base import *  # noqa: F401,F403
from pathlib import Path

# development.py (DEBUG=True)
TEMPLATES = [{
    'APP_DIRS': True,  # Her request'te kontrol eder (hot reload için)
}]

# production.py (DEBUG=False)
TEMPLATES = [{
    'OPTIONS': {
        'loaders': [
            ('django.template.loaders.cached.Loader', [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ]),
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

# Production logging configuration
# Ensure logs directory exists
LOGS_DIR = Path("/app/logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Override base logging for production
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {name} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'security': {
            'format': '{levelname} {asctime} SECURITY {name} {message}',
            'style': '{',
        },
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(levelname)s %(asctime)s %(name)s %(message)s'
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': LOGS_DIR / 'tosca_api.log',
            'formatter': 'json',
        },
        'security_file': {
            'level': 'WARNING',
            'class': 'logging.FileHandler',
            'filename': LOGS_DIR / 'security.log',
            'formatter': 'json',
        },
        'error_file': {
            'level': 'ERROR',
            'class': 'logging.FileHandler',
            'filename': LOGS_DIR / 'errors.log',
            'formatter': 'verbose',
        },
        'null': {
            'class': 'logging.NullHandler',
        },
    },
    'root': {
        'handlers': ['file', 'error_file'],
        'level': 'WARNING',
    },
    'loggers': {
        'tosca_api': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': False,
        },
        'tosca_api.apps.authentication': {
            'handlers': ['security_file', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['security_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['error_file'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}


