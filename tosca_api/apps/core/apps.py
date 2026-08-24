from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tosca_api.apps.core"

    def ready(self):
        from . import checks  # noqa: F401
        from . import content_asset_signals  # noqa: F401
        from . import media_lifecycle_signals  # noqa: F401
