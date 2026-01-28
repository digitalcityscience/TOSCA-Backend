"""Development settings."""
from .base import *  # noqa: F401,F403


DEBUG = True

# Allow Postman reverse DNS lookup
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '1.0.0.127.in-addr.arpa']

# Django auth redirects
LOGIN_REDIRECT_URL = '/welcome/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# Allauth settings for social login
ACCOUNT_LOGIN_REDIRECT_URL = '/welcome/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/accounts/login/'
SOCIALACCOUNT_LOGIN_REDIRECT_URL = '/welcome/'
ACCOUNT_SHOW_SUCCESS_PAGE = False  # Skip allauth success page, redirect immediately

# Development-specific logging (more verbose, console-only)
LOGGING['handlers']['console']['level'] = 'DEBUG'  # noqa: F405
LOGGING['handlers']['console']['formatter'] = 'verbose'  # noqa: F405
LOGGING['loggers']['tosca_api']['level'] = 'DEBUG'  # noqa: F405
LOGGING['loggers']['tosca_api.apps.authentication']['level'] = 'DEBUG'  # noqa: F405

# Disable file logging in development (console only)
for logger_config in LOGGING['loggers'].values():  # noqa: F405
    if 'file' in logger_config.get('handlers', []):
        logger_config['handlers'] = [h for h in logger_config['handlers'] if 'file' not in h]
    if 'security_file' in logger_config.get('handlers', []):
        logger_config['handlers'] = [h for h in logger_config['handlers'] if 'security_file' not in h]

