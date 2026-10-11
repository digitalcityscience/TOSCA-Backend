"""Storage side effects of simulation rows."""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import SimulationAsset, SimulationComparison, SimulationRun

logger = logging.getLogger(__name__)

_PRIOR_STATUS = "_geosim_prior_status"


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


@receiver(pre_save, sender=SimulationRun)
@receiver(pre_save, sender=SimulationComparison)
def capture_prior_status(sender, instance, **kwargs) -> None:
    prior = None
    if instance.pk is not None:
        prior = sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
    setattr(instance, _PRIOR_STATUS, prior)


@receiver(post_save, sender=SimulationRun)
@receiver(post_save, sender=SimulationComparison)
def move_files_on_status_change(sender, instance, created, **kwargs) -> None:
    prior = getattr(instance, _PRIOR_STATUS, None)
    if created or prior is None or prior == instance.status:
        return
    owner_id = instance.pk

    def sync() -> None:
        from .lifecycle import sync_run_storage

        owner = sender.objects.filter(pk=owner_id).first()
        if owner is not None:
            sync_run_storage(owner)

    # After commit: a rolled-back status change must not move files.
    transaction.on_commit(sync)
