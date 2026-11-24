"""Celery tasks of the external catalog (``TOSCA_TASK_BACKEND="celery"``). Ids only."""

from celery import shared_task

from . import harvest, health


@shared_task(name="external_catalog.harvest")
def harvest_service(service_pk) -> None:
    harvest.run_by_pk(service_pk)


@shared_task(name="external_catalog.health")
def check_items(item_ids) -> None:
    health.run_for_ids(item_ids)
