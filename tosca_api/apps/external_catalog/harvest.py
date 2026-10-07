"""Source index load ("Load catalog") for the category picker (ticket 03b).

Reads everything a service offers -- every OGC collection with its dataset's
themes, or every SensorThings layer -- into :class:`ServiceSource`, so
composing a category never waits for the remote service. For Hamburg's OGC
API that is one or two requests per dataset (~1-2 min), longer than a
proxied request may last, so the admin starts it in a background thread and
polls the state stored on the service. The state lives in the database
because the admin may be served by several processes.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from django.db import close_old_connections, connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from . import remote
from .models import CategoryItem, ExternalService, ServiceSource

logger = logging.getLogger(__name__)

# A run that has not finished after this long is assumed dead (e.g. the
# process was restarted) and may be started again.
STALE_AFTER = timedelta(minutes=15)


def _indexer(service: ExternalService):
    if service.service_type == ExternalService.ServiceType.SENSORTHINGS:
        return remote.index_sta_sources
    return remote.index_ogc_sources


def state(service: ExternalService) -> dict:
    """``{"state": "never" | "running" | "done" | "failed", ...}`` for the picker."""
    started, finished = service.catalog_load_started_at, service.catalog_loaded_at
    if _is_running(service):
        value = "running"
    elif service.catalog_load_error:
        value = "failed"
    elif finished:
        value = "done"
    else:
        value = "never"
    return {
        "state": value,
        "started_at": started.isoformat() if started else None,
        "loaded_at": finished.isoformat() if finished else None,
        "error": service.catalog_load_error or None,
        "note": service.catalog_load_note or None,
    }


def _is_running(service: ExternalService) -> bool:
    """Started recently, and neither finished nor failed since."""
    started, finished = service.catalog_load_started_at, service.catalog_loaded_at
    if started is None or started < timezone.now() - STALE_AFTER or service.catalog_load_error:
        return False
    return finished is None or finished < started


def claim(service: ExternalService) -> bool:
    """Mark a run as started unless one is already running (atomic)."""
    now = timezone.now()
    not_running = (
        Q(catalog_load_started_at__isnull=True)
        | Q(catalog_load_started_at__lt=now - STALE_AFTER)
        | Q(catalog_loaded_at__gte=F("catalog_load_started_at"))
        | ~Q(catalog_load_error="")
    )
    claimed = (
        ExternalService.objects.filter(pk=service.pk)
        .filter(not_running)
        .update(catalog_load_started_at=now, catalog_load_error="")
    )
    if claimed:
        service.catalog_load_started_at, service.catalog_load_error = now, ""
    return bool(claimed)


def run(service: ExternalService) -> list[dict]:
    """Index now and store the result; records success or failure on the service."""
    try:
        rows, problems = _indexer(service)(service)
        store(service, rows)
    except Exception as exc:
        message = str(exc) if isinstance(exc, remote.RemoteServiceError) else "Unexpected error, see the server log."
        if not isinstance(exc, remote.RemoteServiceError):
            logger.exception("Catalog load failed for service %s", service.pk)
        ExternalService.objects.filter(pk=service.pk).update(catalog_load_error=message)
        service.catalog_load_error = message
        raise
    note = _note(problems)
    now = timezone.now()
    ExternalService.objects.filter(pk=service.pk).update(
        catalog_loaded_at=now, catalog_load_error="", catalog_load_note=note
    )
    service.catalog_loaded_at, service.catalog_load_error, service.catalog_load_note = now, "", note
    return rows


def _note(problems: list[str]) -> str:
    if not problems:
        return ""
    shown = "; ".join(problems[:5])
    more = f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""
    return f"{len(problems)} dataset(s) could not be read: {shown}{more}"


def start_in_background(service: ExternalService) -> bool:
    """Start a load unless one is running; returns whether one was started."""
    if not claim(service):
        return False
    service_pk = service.pk
    # Start after the request's transaction commits so the claim is visible.
    transaction.on_commit(lambda: _spawn(service_pk))
    return True


def _spawn(service_pk) -> None:
    threading.Thread(target=_background, args=(service_pk,), name="external-catalog-load", daemon=True).start()


def _background(service_pk) -> None:
    close_old_connections()
    try:
        run_by_pk(service_pk)
    finally:
        connection.close()


def run_by_pk(service_pk) -> None:
    """Thread body: failures are already recorded on the service by :func:`run`."""
    try:
        run(ExternalService.objects.get(pk=service_pk))
    except remote.RemoteServiceError:
        pass
    except Exception:
        logger.exception("Catalog load thread failed for service %s", service_pk)


@transaction.atomic
def store(service: ExternalService, rows: list[dict]) -> None:
    """Replace the service's index with ``rows`` and refresh item copies."""
    now = timezone.now()
    keys = {(row["dataset_id"], row["source_id"]) for row in rows}
    stale = [
        pk
        for pk, dataset_id, source_id in ServiceSource.objects.filter(service=service).values_list(
            "pk", "dataset_id", "source_id"
        )
        if (dataset_id, source_id) not in keys
    ]
    ServiceSource.objects.filter(pk__in=stale).delete()
    ServiceSource.objects.bulk_create(
        [
            ServiceSource(
                service=service,
                dataset_id=row["dataset_id"],
                dataset_title=row["dataset_title"][:500],
                source_id=row["source_id"],
                title=row["title"][:500],
                description=row["description"],
                item_type=(row["item_type"] or "")[:50],
                geojson=row["geojson"],
                count=row["count"],
                themes=row["themes"],
                harvested_at=now,
            )
            for row in rows
        ],
        update_conflicts=True,
        unique_fields=["service", "dataset_id", "source_id"],
        update_fields=[
            "dataset_title",
            "title",
            "description",
            "item_type",
            "geojson",
            "count",
            "themes",
            "harvested_at",
        ],
        batch_size=500,
    )
    refresh_items(service)


def refresh_items(service: ExternalService) -> int:
    """Copy current titles/descriptions from the index onto the service's items."""
    sources = {
        (source.dataset_id, source.source_id): source for source in ServiceSource.objects.filter(service=service)
    }
    changed = []
    for item in CategoryItem.objects.filter(service=service).select_related("service"):
        source = sources.get(item_source_key(item))
        if source is None:
            continue  # gone at the service; the health check (ticket 05) flags it
        values = item_values(source)
        if any(getattr(item, field) != value for field, value in values.items()):
            for field, value in values.items():
                setattr(item, field, value)
            changed.append(item)
    CategoryItem.objects.bulk_update(changed, ["title", "description", "dataset_title"])
    return len(changed)


def item_source_key(item: CategoryItem) -> tuple[str, str]:
    """``(dataset_id, source_id)`` of an item, in :class:`ServiceSource` terms."""
    if item.service.service_type == ExternalService.ServiceType.SENSORTHINGS:
        return item.sta_service_name, item.sta_layer_name
    return item.ogc_dataset_id, item.ogc_collection_id


def item_values(source: ServiceSource) -> dict:
    """Item fields copied from an indexed source."""
    return {
        "title": source.title[:200],
        "description": source.description,
        "dataset_title": source.dataset_title,
    }


def source_fields(source: ServiceSource) -> dict:
    """Source fields of a new item for ``source``."""
    if source.service.service_type == ExternalService.ServiceType.SENSORTHINGS:
        return {"sta_service_name": source.dataset_id, "sta_layer_name": source.source_id}
    return {"ogc_dataset_id": source.dataset_id, "ogc_collection_id": source.source_id}
