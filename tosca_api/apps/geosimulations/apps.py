from django.apps import AppConfig


class GeoSimulationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tosca_api.apps.geosimulations"
    verbose_name = "GeoSimulations"

    def ready(self) -> None:
        from . import signals  # noqa: F401
