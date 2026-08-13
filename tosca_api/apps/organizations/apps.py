from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tosca_api.apps.organizations"
    verbose_name = "Organizations"

    def ready(self):
        from . import signals  # noqa: F401
