from django.apps import AppConfig


class ExternalCatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tosca_api.apps.external_catalog"
    label = "external_catalog"
    verbose_name = "External catalog"
