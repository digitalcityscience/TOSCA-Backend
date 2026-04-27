from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tosca_api.apps.console'
    verbose_name = 'Console'
