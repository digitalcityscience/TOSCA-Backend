from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tosca_api.apps.events"
    verbose_name = "Calendar Events"
