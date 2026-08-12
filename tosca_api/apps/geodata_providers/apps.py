from django.apps import AppConfig


class GeodataProvidersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tosca_api.apps.geodata_providers'
    verbose_name = 'Geodata Providers'

    def ready(self):
        from . import signals  # noqa: F401