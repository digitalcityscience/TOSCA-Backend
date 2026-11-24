"""Celery app. Only used when ``TOSCA_TASK_BACKEND="celery"`` (optional extra)."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tosca_api.settings.production")

app = Celery("tosca_api")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
