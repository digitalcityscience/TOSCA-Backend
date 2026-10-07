"""Item health check (external catalog ticket 05).

Checks that each category item's source still exists at its service and
records how many features (OGC) or datastreams (SensorThings) it has:

* OGC: the collection's metadata (exists? offers GeoJSON items?) and
  ``numberMatched`` of a ``limit=1`` items request.
* SensorThings: datastream ids filtered by ``serviceName``/``layerName``,
  paged with ``$top=1000`` -- never ``$count``, which takes ~28 s on
  Hamburg with property filters.

Results go to ``availability_state``, ``feature_count``, ``last_checked_at``
and ``last_check_error``; a failing item or service never stops the run.
Runs from the admin (background thread, like "Load catalog") or from
``manage.py check_external_catalog``.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from . import harvest, remote
from .models import CategoryItem, ExternalService

logger = logging.getLogger(__name__)

PARALLEL_CHECKS = 4
STA_PAGE_SIZE = 1000
STA_MAX_PAGES = 100

State = CategoryItem.AvailabilityState


def check_item(item: CategoryItem) -> tuple[str, int | None, str]:
    """``(state, feature_count, error)`` for one item; never raises."""
    try:
        if item.service.service_type == ExternalService.ServiceType.SENSORTHINGS:
            return _check_sensorthings(item)
        return _check_ogc(item)
    except remote.RemoteServiceError as exc:
        if exc.status == 404:
            return State.MISSING, None, str(exc)
        return State.ERROR, None, str(exc)
    except Exception:  # noqa: BLE001 - one broken item must not stop the run
        logger.exception("Health check failed for item %s", item.pk)
        return State.ERROR, None, "Unexpected error, see the server log."


def _check_ogc(item: CategoryItem) -> tuple[str, int | None, str]:
    service = item.service
    landing = remote._landing_page_url(service, item.ogc_dataset_id, refresh=False)
    collection_url = f"{landing}/collections/{quote(item.ogc_collection_id, safe='')}"
    collection = remote.fetch_json(service, collection_url, {"f": "json"})
    if not isinstance(collection, dict) or not remote._has_geojson_items(collection.get("links")):
        return State.ERROR, None, "The collection offers no GeoJSON items and cannot be shown on the map."
    items = remote.fetch_json(service, f"{collection_url}/items", {"f": "json", "limit": "1"})
    matched = items.get("numberMatched") if isinstance(items, dict) else None
    return State.OK, matched if isinstance(matched, int) else None, ""


def _check_sensorthings(item: CategoryItem) -> tuple[str, int | None, str]:
    service = item.service
    base = remote.sta_base_url(service)
    filter_ = (
        f"properties/serviceName eq {remote._odata_string(item.sta_service_name)} "
        f"and properties/layerName eq {remote._odata_string(item.sta_layer_name)}"
    )
    total = 0
    for page in range(STA_MAX_PAGES):
        data = remote.fetch_json(
            service,
            f"{base}/Datastreams",
            {
                "$filter": filter_,
                "$select": "@iot.id",
                "$orderby": "@iot.id",
                "$top": str(STA_PAGE_SIZE),
                "$skip": str(page * STA_PAGE_SIZE),
            },
        )
        values = data.get("value") if isinstance(data, dict) else None
        if not isinstance(values, list):
            raise remote.RemoteServiceError("Unexpected response from the service.")
        total += len(values)
        if len(values) < STA_PAGE_SIZE:
            break
    else:
        return State.ERROR, None, f"More than {STA_MAX_PAGES * STA_PAGE_SIZE} datastreams; not counted."
    if total == 0:
        return State.MISSING, 0, "No datastreams with this serviceName and layerName."
    return State.OK, total, ""


def check_items(items) -> dict[str, int]:
    """Check ``items`` (in parallel), store the results, return counts per state."""
    items = list(items)
    with ThreadPoolExecutor(max_workers=PARALLEL_CHECKS) as pool:
        results = list(pool.map(check_item, items))
    now = timezone.now()
    summary: dict[str, int] = {}
    for item, (state, count, error) in zip(items, results, strict=True):
        fields = {"availability_state": state, "last_checked_at": now, "last_check_error": error}
        if count is not None or state == State.MISSING:
            fields["feature_count"] = count
        CategoryItem.objects.filter(pk=item.pk).update(**fields)
        summary[state] = summary.get(state, 0) + 1
    for service in {item.service for item in items}:
        harvest.refresh_items(service)
    return summary


def items_for(*, services=None, categories=None, item_ids=None):
    queryset = CategoryItem.objects.select_related("service", "category").order_by("service_id", "display_order")
    if services is not None:
        queryset = queryset.filter(service__in=services)
    if categories is not None:
        queryset = queryset.filter(category__in=categories)
    if item_ids is not None:
        queryset = queryset.filter(pk__in=item_ids)
    return queryset


def start_in_background(item_ids) -> int:
    """Check these items in a background thread after the request commits."""
    item_ids = list(item_ids)
    if item_ids:
        transaction.on_commit(lambda: _spawn(item_ids))
    return len(item_ids)


def _spawn(item_ids) -> None:
    threading.Thread(target=_background, args=(item_ids,), name="external-catalog-check", daemon=True).start()


def _background(item_ids) -> None:
    close_old_connections()
    try:
        run_for_ids(item_ids)
    finally:
        connection.close()


def run_for_ids(item_ids) -> None:
    try:
        check_items(items_for(item_ids=item_ids))
    except Exception:
        logger.exception("Background health check failed")
