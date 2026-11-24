try:  # Celery is an optional dependency (TOSCA_TASK_BACKEND="celery").
    from .celery import app as celery_app
except ImportError:  # pragma: no cover - default install has no celery
    celery_app = None
else:
    __all__ = ("celery_app",)
