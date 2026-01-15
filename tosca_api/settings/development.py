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

