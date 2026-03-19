from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from .models import Event


def apply_event_filters(queryset, *, filters: dict):
    """Apply the shared event filter contract to a queryset."""
    status_value = filters.get("status", Event.Status.PUBLISHED)
    queryset = queryset.filter(status=status_value)

    campaign_id = filters.get("campaign_id")
    if campaign_id:
        queryset = queryset.filter(campaign_id=campaign_id)

    visibility = filters.get("visibility")
    if visibility:
        queryset = queryset.filter(visibility=visibility)

    include_past = filters.get("include_past", False)
    if not include_past:
        queryset = queryset.filter(start_datetime__gte=timezone.now())

    start_after = filters.get("start_after")
    if start_after:
        queryset = queryset.filter(start_datetime__gte=start_after)

    start_before = filters.get("start_before")
    if start_before:
        queryset = queryset.filter(start_datetime__lte=start_before)

    term_id = filters.get("term_id")
    if term_id:
        queryset = queryset.filter(event_terms__term_id=term_id)

    dimension_id = filters.get("dimension_id")
    if dimension_id:
        queryset = queryset.filter(event_terms__term__dimension_id=dimension_id)

    spatial_geometry = filters.get("spatial_geometry")
    if spatial_geometry is not None:
        queryset = queryset.filter(
            Q(
                location_mode__in=[
                    Event.LocationMode.PHYSICAL,
                    Event.LocationMode.HYBRID,
                ],
                location__isnull=False,
                location__within=spatial_geometry,
            )
            | Q(location_mode=Event.LocationMode.ONLINE)
        )

    if term_id or dimension_id:
        queryset = queryset.distinct()

    return queryset
