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

    event_type_id = filters.get("event_type_id")
    if event_type_id:
        queryset = queryset.filter(event_type_id=event_type_id)

    if "profile_key" in filters:
        profile_key = filters.get("profile_key") or ""
        if profile_key:
            queryset = queryset.filter(event_type__profile_key=profile_key)
        else:
            queryset = queryset.filter(
                Q(event_type__isnull=True)
                | Q(event_type__profile_key__isnull=True)
                | Q(event_type__profile_key="")
            )

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

    dimension_code = filters.get("dimension_code")
    if dimension_code:
        queryset = queryset.filter(event_terms__term__dimension__code=dimension_code)

    term_code = filters.get("term_code")
    if term_code:
        queryset = queryset.filter(event_terms__term__code=term_code)

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
            | Q(
                location_mode__in=[
                    Event.LocationMode.ONLINE,
                    Event.LocationMode.BY_ARRANGEMENT,
                    Event.LocationMode.HOME_VISIT,
                ]
            )
        )

    if term_id or dimension_id or dimension_code or term_code:
        queryset = queryset.distinct()

    return queryset
