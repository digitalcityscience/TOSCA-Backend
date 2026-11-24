"""Which background task backend runs, in one place (``TOSCA_TASK_BACKEND``)."""

import threading

from django.conf import settings
from django.utils.module_loading import import_string

DATABASE = "database"
CELERY = "celery"
BACKENDS = (DATABASE, CELERY)


def uses_celery() -> bool:
    return getattr(settings, "TOSCA_TASK_BACKEND", DATABASE) == CELERY


def run_in_background(thread_target, task_path: str, *args, name: str) -> None:
    """Run ``thread_target(*args)`` in a daemon thread, or enqueue the Celery task ``task_path``.

    Arguments must be plain ids: the task reloads everything from the database.
    """
    if uses_celery():
        import_string(task_path).delay(*args)
        return
    threading.Thread(target=thread_target, args=args, name=name, daemon=True).start()
