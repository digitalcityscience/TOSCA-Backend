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

"""

DEBUG = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True


