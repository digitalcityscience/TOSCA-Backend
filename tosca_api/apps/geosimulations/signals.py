"""Remove stored objects when their asset rows are deleted."""

import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import SimulationAsset

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=SimulationAsset)
def delete_stored_object(sender, instance: SimulationAsset, **kwargs) -> None:
    storage, path = instance.storage, instance.storage_path

    def delete() -> None:
        try:
            storage.delete(path)
        except Exception:  # storage outages must not break the delete that already committed
            logger.exception("Could not delete simulation asset object %s", path)

    # After commit only: a rolled-back delete must keep its file.
    transaction.on_commit(delete)
