from __future__ import annotations

from calendar import monthrange
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    CultureEventProfile,
    Event,
    EventSeries,
    EventSeriesDate,
    EventTerm,
    TaxonomyDimension,
    EventType,
    PublicHealthEventProfile,
    SportsEventProfile,
    TaxonomyTerm,
)

KEEP_EXISTING_TERMS = object()


def validate_publish_requirements(event_data: dict) -> dict:
    """Return a dict of field->error for publish-time rules.

    Applied at the serializer/admin-form boundary, not on the model itself,
    so that ad-hoc Event.objects.create() in tests stays lightweight.
    """
    from .models import Event  # local import to avoid circular import at module load

    errors: dict[str, str] = {}
    if event_data.get("status") != Event.Status.PUBLISHED:
        return errors

    if not event_data.get("summary"):
        errors["summary"] = "A summary is required to publish an event."

    has_contact = any(
        event_data.get(field)
        for field in ("provider_phone", "provider_email", "provider_social", "provider_url")
    )
    if not has_contact:
        errors["provider_phone"] = (
            "Published events require at least one provider contact "
            "(phone, email, social, or url)."
        )

    return errors

EVENT_TEMPLATE_FIELDS = frozenset({
    "title",
    "summary",
    "location_mode",
    "location",
    "venue_address",
    "district",
    "online_url",
    "online_platform",
    "access_notes",
    "provider_name",
    "provider_address",
    "provider_phone",
    "provider_email",
    "provider_social",
    "provider_url",
    "language",
    "language_note",
    "lead_name",
    "external_url",
    "status",
    "visibility",
    "context",
})

WEEKDAY_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True, slots=True)
class OccurrenceSpec:
    occurrence_index: int
    occurrence_date: date
    start_datetime: datetime
    end_datetime: datetime
    original_start_datetime: datetime


@dataclass(frozen=True, slots=True)
class SyncResult:
    created_count: int
    updated_count: int
    deleted_count: int
    skipped_exception_count: int
    events: list[Event]


def build_occurrence_specs(
    series: EventSeries,
    *,
    explicit_dates: list[date] | None = None,
) -> list[OccurrenceSpec]:
    tz = ZoneInfo(series.timezone)
    duration = _build_occurrence_duration(series, tz)
    occurrence_dates = _generate_occurrence_dates(series, explicit_dates=explicit_dates)

    occurrences: list[OccurrenceSpec] = []
    for index, occurrence_date in enumerate(occurrence_dates, start=1):
        start_datetime = _localize(series, occurrence_date, tz)
        occurrences.append(
            OccurrenceSpec(
                occurrence_index=index,
                occurrence_date=occurrence_date,
                start_datetime=start_datetime,
                end_datetime=start_datetime + duration,
                original_start_datetime=start_datetime,
            )
        )
    return occurrences


def create_occurrence_events(
    *,
    series: EventSeries,
    occurrences: list[OccurrenceSpec],
    event_template: dict[str, Any],
    organizer,
    taxonomy_terms: list[TaxonomyTerm],
) -> list[Event]:
    created_events: list[Event] = []
    with transaction.atomic():
        for occurrence in occurrences:
            event = Event(
                campaign=series.campaign,
                event_type=series.event_type,
                organizer=organizer,
            )
            _apply_occurrence_to_event(
                event,
                occurrence=occurrence,
                template_data=event_template,
                series=series,
            )
            event.save()
            _set_event_terms(event, taxonomy_terms)
            created_events.append(event)

    return created_events


def sync_occurrence_events(
    *,
    series: EventSeries,
    occurrences: list[OccurrenceSpec],
    event_template: dict[str, Any],
    template_event: Event,
    organizer,
    taxonomy_terms: list[TaxonomyTerm] | object = KEEP_EXISTING_TERMS,
    reference_time: datetime | None = None,
) -> SyncResult:
    now = reference_time or timezone.now()
    existing_events = {
        event.occurrence_index: event
        for event in series.events.all().order_by("occurrence_index", "start_datetime")
        if event.occurrence_index is not None
    }
    target_indexes = {occurrence.occurrence_index for occurrence in occurrences}
    created_count = 0
    updated_count = 0
    deleted_count = 0
    skipped_exception_count = 0
    synced_events: list[Event] = []

    with transaction.atomic():
        for occurrence in occurrences:
            event = existing_events.get(occurrence.occurrence_index)
            if event is None:
                if occurrence.start_datetime < now:
                    continue
                event = Event(
                    campaign=series.campaign,
                    event_type=series.event_type,
                    organizer=organizer,
                )
                _apply_occurrence_to_event(
                    event,
                    occurrence=occurrence,
                    template_data=event_template,
                    series=series,
                )
                event.save()
                if taxonomy_terms is KEEP_EXISTING_TERMS:
                    _copy_event_terms(source_event=template_event, target_event=event)
                else:
                    _set_event_terms(event, taxonomy_terms)
                created_count += 1
                synced_events.append(event)
                continue

            if event.start_datetime < now:
                synced_events.append(event)
                continue

            if event.is_exception:
                skipped_exception_count += 1
                synced_events.append(event)
                continue

            _apply_occurrence_to_event(
                event,
                occurrence=occurrence,
                template_data=event_template,
                series=series,
            )
            event.organizer = organizer
            event.save()
            if taxonomy_terms is not KEEP_EXISTING_TERMS:
                _set_event_terms(event, taxonomy_terms)
            updated_count += 1
            synced_events.append(event)

        removable_events = (
            series.events.filter(start_datetime__gte=now, is_exception=False)
            .exclude(occurrence_index__in=target_indexes)
            .order_by("occurrence_index", "start_datetime")
        )
        deleted_count = removable_events.count()
        removable_events.delete()

    current_events = list(series.events.order_by("occurrence_index", "start_datetime"))
    return SyncResult(
        created_count=created_count,
        updated_count=updated_count,
        deleted_count=deleted_count,
        skipped_exception_count=skipped_exception_count,
        events=current_events,
    )


def serialize_occurrence_specs(
    occurrences: list[OccurrenceSpec],
) -> list[dict[str, Any]]:
    return [
        {
            "occurrence_index": occurrence.occurrence_index,
            "occurrence_date": occurrence.occurrence_date,
            "start_datetime": occurrence.start_datetime,
            "end_datetime": occurrence.end_datetime,
            "original_start_datetime": occurrence.original_start_datetime,
        }
        for occurrence in occurrences
    ]


def serialize_occurrence_events(events: list[Event]) -> list[dict[str, Any]]:
    return [
        {
            "id": event.id,
            "occurrence_index": event.occurrence_index,
            "is_exception": event.is_exception,
            "start_datetime": event.start_datetime,
            "end_datetime": event.end_datetime,
            "original_start_datetime": event.original_start_datetime,
            "title": event.title,
        }
        for event in events
    ]


def resolve_taxonomy_assignments(
    taxonomy_assignments: list[dict[str, Any]],
    *,
    allow_inactive_dimension_ids: set[Any] | None = None,
    allow_inactive_term_ids: set[Any] | None = None,
    event_profile_key: str | None = None,
) -> list[TaxonomyTerm]:
    """Validate grouped taxonomy assignments and return the resolved terms.

    ``event_profile_key`` is the ``profile_key`` of the target event's
    ``event_type`` (or ``""`` when the event has no event type). When supplied,
    any taxonomy dimension whose ``profile_key`` is non-empty must match it.
    """
    if not taxonomy_assignments:
        return []

    allow_inactive_dimension_ids = allow_inactive_dimension_ids or set()
    allow_inactive_term_ids = allow_inactive_term_ids or set()

    errors: list[str] = []
    dimension_ids = [assignment["dimension_id"] for assignment in taxonomy_assignments]
    duplicate_dimension_ids = [
        str(dimension_id)
        for dimension_id, count in Counter(dimension_ids).items()
        if count > 1
    ]
    if duplicate_dimension_ids:
        errors.append(
            "Duplicate taxonomy dimensions are not allowed: "
            f"{', '.join(duplicate_dimension_ids)}"
        )

    dimension_map = TaxonomyDimension.objects.in_bulk(dimension_ids)
    missing_dimension_ids = [
        str(dimension_id)
        for dimension_id in dimension_ids
        if dimension_id not in dimension_map
    ]
    if missing_dimension_ids:
        errors.append(
            "Unknown taxonomy dimensions: " f"{', '.join(missing_dimension_ids)}"
        )

    inactive_dimension_ids = [
        str(dimension.id)
        for dimension in dimension_map.values()
        if not dimension.is_active and dimension.id not in allow_inactive_dimension_ids
    ]
    if inactive_dimension_ids:
        errors.append(
            "Inactive taxonomy dimensions cannot be assigned: "
            f"{', '.join(sorted(inactive_dimension_ids))}"
        )

    if event_profile_key is not None:
        mismatched_profile_dimensions = [
            f"{dimension.code} (profile_key='{dimension.profile_key}')"
            for dimension in dimension_map.values()
            if dimension.profile_key
            and dimension.profile_key != event_profile_key
        ]
        if mismatched_profile_dimensions:
            errors.append(
                "Taxonomy dimensions restricted to other profiles cannot be "
                f"assigned: {', '.join(sorted(mismatched_profile_dimensions))}"
            )

    all_term_ids: list[Any] = []
    duplicate_term_ids: list[str] = []
    for assignment in taxonomy_assignments:
        term_ids = assignment["term_ids"]
        if not term_ids:
            errors.append(
                f"Taxonomy dimension {assignment['dimension_id']} must include at least one term."
            )
            continue

        repeated_ids = [
            str(term_id)
            for term_id, count in Counter(term_ids).items()
            if count > 1
        ]
        duplicate_term_ids.extend(repeated_ids)
        all_term_ids.extend(term_ids)

    if duplicate_term_ids:
        errors.append(
            "Duplicate taxonomy term IDs are not allowed within a dimension: "
            f"{', '.join(sorted(set(duplicate_term_ids)))}"
        )

    term_map = TaxonomyTerm.objects.select_related("dimension").in_bulk(all_term_ids)
    missing_term_ids = [
        str(term_id)
        for term_id in all_term_ids
        if term_id not in term_map
    ]
    if missing_term_ids:
        errors.append("Unknown taxonomy terms: " f"{', '.join(missing_term_ids)}")

    inactive_term_ids = [
        str(term.id)
        for term in term_map.values()
        if not term.is_active and term.id not in allow_inactive_term_ids
    ]
    if inactive_term_ids:
        errors.append(
            "Inactive taxonomy terms cannot be assigned: "
            f"{', '.join(sorted(inactive_term_ids))}"
        )

    parent_term_ids = set(
        TaxonomyTerm.objects.filter(parent_id__in=all_term_ids).values_list(
            "parent_id",
            flat=True,
        )
    )
    non_leaf_term_ids = [
        str(term_id) for term_id in all_term_ids if term_id in parent_term_ids
    ]
    if non_leaf_term_ids:
        errors.append(
            "Only leaf taxonomy terms may be assigned: "
            f"{', '.join(non_leaf_term_ids)}"
        )

    resolved_terms: list[TaxonomyTerm] = []
    for assignment in taxonomy_assignments:
        dimension_id = assignment["dimension_id"]
        dimension = dimension_map.get(dimension_id)
        term_ids = assignment["term_ids"]
        resolved_dimension_terms = [term_map[term_id] for term_id in term_ids if term_id in term_map]

        if dimension is None:
            continue

        mismatched_terms = [
            str(term.id)
            for term in resolved_dimension_terms
            if term.dimension_id != dimension_id
        ]
        if mismatched_terms:
            errors.append(
                "Taxonomy terms do not belong to dimension "
                f"{dimension_id}: {', '.join(mismatched_terms)}"
            )

        if (
            dimension.selection_mode == TaxonomyDimension.SelectionMode.SINGLE
            and len(resolved_dimension_terms) > 1
        ):
            errors.append(
                f"Single-select taxonomy dimension {dimension_id} allows only one term."
            )

        resolved_terms.extend(
            term for term in resolved_dimension_terms if term.dimension_id == dimension_id
        )

    if errors:
        raise ValidationError({"taxonomy_assignments": errors})

    return resolved_terms


def serialize_taxonomy_assignments(
    taxonomy_terms: list[TaxonomyTerm],
) -> list[dict[str, Any]]:
    """Group taxonomy terms by dimension for read hydration."""
    grouped_assignments: dict[Any, dict[str, Any]] = {}
    ordered_terms = sorted(
        taxonomy_terms,
        key=lambda term: (
            term.dimension.sort_order,
            term.dimension.label,
            term.sort_order,
            term.label,
        ),
    )

    for term in ordered_terms:
        group = grouped_assignments.setdefault(
            term.dimension_id,
            {
                "dimension_id": term.dimension_id,
                "dimension_code": term.dimension.code,
                "dimension_label": term.dimension.label,
                "selection_mode": term.dimension.selection_mode,
                "profile_key": term.dimension.profile_key,
                "term_ids": [],
                "terms": [],
            },
        )
        group["term_ids"].append(term.id)
        group["terms"].append(
            {
                "id": term.id,
                "code": term.code,
                "label": term.label,
                "parent_id": term.parent_id,
                "is_active": term.is_active,
            }
        )

    return list(grouped_assignments.values())


def get_event_taxonomy_assignments(event: Event) -> list[dict[str, Any]]:
    """Return grouped taxonomy assignments for an event."""
    taxonomy_terms = list(
        TaxonomyTerm.objects.select_related("dimension")
        .filter(event_terms__event=event)
        .distinct()
    )
    return serialize_taxonomy_assignments(taxonomy_terms)


def _apply_occurrence_to_event(
    event: Event,
    *,
    occurrence: OccurrenceSpec,
    template_data: dict[str, Any],
    series: EventSeries,
) -> None:
    for field, value in template_data.items():
        setattr(event, field, value)

    event.campaign = series.campaign
    event.event_type = series.event_type
    event.series = series
    event.occurrence_index = occurrence.occurrence_index
    event.start_datetime = occurrence.start_datetime
    event.end_datetime = occurrence.end_datetime
    event.original_start_datetime = occurrence.original_start_datetime
    event.is_exception = False


def _set_event_terms(event: Event, taxonomy_terms: list[TaxonomyTerm]) -> None:
    EventTerm.objects.filter(event=event).delete()
    for term in taxonomy_terms:
        EventTerm.objects.create(event=event, term=term)


def _copy_event_terms(source_event: Event, target_event: Event) -> None:
    source_terms = list(
        TaxonomyTerm.objects.filter(event_terms__event=source_event).distinct()
    )
    _set_event_terms(target_event, source_terms)


def _build_occurrence_duration(series: EventSeries, tz: ZoneInfo) -> timedelta:
    start_datetime = datetime.combine(series.start_date, series.start_time, tzinfo=tz)
    end_datetime = datetime.combine(series.start_date, series.end_time, tzinfo=tz)
    return end_datetime - start_datetime


def _localize(series: EventSeries, occurrence_date: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(occurrence_date, series.start_time, tzinfo=tz)


def _generate_occurrence_dates(
    series: EventSeries,
    *,
    explicit_dates: list[date] | None = None,
) -> list[date]:
    if series.series_mode == EventSeries.SeriesMode.MANUAL_BATCH:
        if explicit_dates is not None:
            return explicit_dates
        return list(
            series.dates.order_by("display_order", "occurrence_date").values_list(
                "occurrence_date",
                flat=True,
            )
        )

    if series.recurrence_type == EventSeries.RecurrenceType.DAILY:
        return _generate_daily_dates(series)
    if series.recurrence_type == EventSeries.RecurrenceType.WEEKLY:
        return _generate_weekly_dates(series)
    if series.recurrence_type == EventSeries.RecurrenceType.MONTHLY:
        return _generate_monthly_dates(series)
    return []


def _generate_daily_dates(series: EventSeries) -> list[date]:
    dates: list[date] = []
    current_date = series.start_date
    while _can_continue(dates, current_date, series):
        dates.append(current_date)
        current_date += timedelta(days=series.interval)
    return dates


def _generate_weekly_dates(series: EventSeries) -> list[date]:
    dates: list[date] = []
    anchor_week_start = series.start_date - timedelta(days=series.start_date.weekday())
    weekday_indexes = sorted({WEEKDAY_TO_INDEX[weekday] for weekday in series.by_weekday})
    week_offset = 0

    while True:
        week_start = anchor_week_start + timedelta(weeks=week_offset)
        candidates = [
            week_start + timedelta(days=weekday_index)
            for weekday_index in weekday_indexes
        ]
        candidates = [candidate for candidate in candidates if candidate >= series.start_date]

        if series.end_date and candidates and min(candidates) > series.end_date:
            break

        for candidate in candidates:
            if not _can_continue(dates, candidate, series):
                return dates
            dates.append(candidate)

        week_offset += series.interval

    return dates


def _generate_monthly_dates(series: EventSeries) -> list[date]:
    dates: list[date] = []
    month_offset = 0

    while True:
        year, month = _add_months(series.start_date.year, series.start_date.month, month_offset)
        candidate = _monthly_candidate(series, year, month)
        if candidate and candidate >= series.start_date:
            if not _can_continue(dates, candidate, series):
                return dates
            dates.append(candidate)

        if series.end_date:
            last_of_month = date(year, month, monthrange(year, month)[1])
            if last_of_month >= series.end_date and (
                candidate is None or candidate > series.end_date
            ):
                break

        month_offset += series.interval

    return dates


def _monthly_candidate(series: EventSeries, year: int, month: int) -> date | None:
    if series.monthly_rule_type == EventSeries.MonthlyRuleType.DAY_OF_MONTH:
        days_in_month = monthrange(year, month)[1]
        if series.day_of_month > days_in_month:
            return None
        return date(year, month, series.day_of_month)

    weekday_index = WEEKDAY_TO_INDEX[series.weekday_of_month]
    first_weekday, days_in_month = monthrange(year, month)
    first_target_day = 1 + ((weekday_index - first_weekday) % 7)
    day = first_target_day + ((series.week_of_month - 1) * 7)
    if day > days_in_month:
        return None
    return date(year, month, day)


def _add_months(year: int, month: int, month_offset: int) -> tuple[int, int]:
    zero_based_month = month - 1 + month_offset
    return year + (zero_based_month // 12), (zero_based_month % 12) + 1


def _can_continue(existing_dates: list[date], candidate: date, series: EventSeries) -> bool:
    if series.end_date and candidate > series.end_date:
        return False
    if series.occurrence_count and len(existing_dates) >= series.occurrence_count:
        return False
    return True


# =============================================================================
# Orchestration functions — shared between admin and API
# =============================================================================


def resolve_series_navigation(event: Event) -> dict | None:
    """Return the rich series widget payload for an event detail response.

    Returns None for standalone events. Result is cached on the instance
    (``event._series_nav``) so list/detail rendering does not re-query the
    sibling occurrences once it has been resolved.
    """
    if not event.series_id:
        return None

    cached = getattr(event, "_series_nav", None)
    if cached is not None:
        return cached

    series = event.series
    siblings = list(
        series.events.only(
            "id",
            "occurrence_index",
            "start_datetime",
        ).order_by("occurrence_index", "start_datetime")
    )

    previous_occurrence = None
    next_occurrence = None
    for sibling in siblings:
        if sibling.occurrence_index is None or event.occurrence_index is None:
            continue
        if sibling.occurrence_index < event.occurrence_index:
            previous_occurrence = sibling
        elif sibling.occurrence_index > event.occurrence_index and next_occurrence is None:
            next_occurrence = sibling

    def _ref(sibling: Event | None) -> dict | None:
        if sibling is None:
            return None
        return {"id": str(sibling.id), "start_datetime": sibling.start_datetime}

    payload = {
        "id": str(series.id),
        "name": series.name,
        "occurrence_index": event.occurrence_index,
        "total_occurrences": len(siblings),
        "is_exception": event.is_exception,
        "original_start_datetime": event.original_start_datetime,
        "previous_occurrence": _ref(previous_occurrence),
        "next_occurrence": _ref(next_occurrence),
    }
    event._series_nav = payload
    return payload


def get_base_template_event(series: EventSeries) -> Event | None:
    """Return the first non-exception occurrence to use as a template for updates.

    Falls back to the first occurrence of any kind if no non-exception exists.
    Returns None for series with no occurrences yet.
    """
    base_event = (
        series.events.filter(is_exception=False)
        .order_by("occurrence_index", "start_datetime")
        .prefetch_related("event_terms__term")
        .first()
    )
    if base_event is not None:
        return base_event
    return (
        series.events.order_by("occurrence_index", "start_datetime")
        .prefetch_related("event_terms__term")
        .first()
    )


def validate_event_template(
    *,
    series: EventSeries,
    event_template: dict[str, Any],
    organizer,
    explicit_dates: list[date] | None = None,
) -> list[OccurrenceSpec]:
    """Validate series + template and return occurrence specs.

    Raises ``ValidationError`` if the series recurrence rules, event template
    fields, or combined exemplar event fail validation.
    """
    series.clean()

    try:
        occurrences = build_occurrence_specs(series, explicit_dates=explicit_dates)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError({"timezone": f"Unknown timezone: {exc}"}) from exc

    if not occurrences:
        raise ValidationError(
            {"explicit_dates": "This series definition produces no occurrences."}
        )

    exemplar_event = Event(
        campaign=series.campaign,
        event_type=series.event_type,
        organizer=organizer,
        series=series if series.pk else None,
        occurrence_index=occurrences[0].occurrence_index,
        original_start_datetime=occurrences[0].original_start_datetime,
        start_datetime=occurrences[0].start_datetime,
        end_datetime=occurrences[0].end_datetime,
        **event_template,
    )
    exemplar_event.clean()

    return occurrences


def orchestrate_series_create(
    *,
    series: EventSeries,
    event_template: dict[str, Any],
    organizer,
    taxonomy_terms: list[TaxonomyTerm] | None = None,
    explicit_dates: list[date] | None = None,
    profile_data: dict[str, Any] | None = None,
) -> list[Event]:
    """Generate occurrence events for a newly created series.

    The series must already be saved (have a PK). Explicit dates should
    already be persisted as ``EventSeriesDate`` rows for manual-batch series.
    """
    occurrences = build_occurrence_specs(series, explicit_dates=explicit_dates)
    created_events = create_occurrence_events(
        series=series,
        occurrences=occurrences,
        event_template=event_template,
        organizer=organizer,
        taxonomy_terms=taxonomy_terms or [],
    )
    if profile_data:
        apply_profiles_to_events(
            events=created_events,
            event_type=series.event_type,
            profile_data=profile_data,
        )
    return created_events


def orchestrate_series_update(
    *,
    series: EventSeries,
    event_template: dict[str, Any],
    organizer,
    taxonomy_terms: list[TaxonomyTerm] | object = KEEP_EXISTING_TERMS,
    explicit_dates: list[date] | None = None,
    profile_data: dict[str, Any] | None = None,
) -> SyncResult:
    """Synchronize occurrence events for an existing generated series.

    The series must already be saved. Explicit dates should already be
    persisted as ``EventSeriesDate`` rows for manual-batch series.
    """
    template_event = get_base_template_event(series)
    occurrences = build_occurrence_specs(series, explicit_dates=explicit_dates)
    sync_result = sync_occurrence_events(
        series=series,
        occurrences=occurrences,
        event_template=event_template,
        template_event=template_event,
        organizer=organizer,
        taxonomy_terms=taxonomy_terms,
    )
    if profile_data:
        non_exception_events = [
            event for event in sync_result.events
            if not event.is_exception
        ]
        apply_profiles_to_events(
            events=non_exception_events,
            event_type=series.event_type,
            profile_data=profile_data,
        )
    return sync_result


def apply_profiles_to_events(
    events: list[Event],
    event_type: EventType | None,
    profile_data: dict[str, Any],
) -> None:
    """Create or update profile extension rows for each event based on event type."""
    if not event_type or not events:
        return

    profile_key = (
        event_type.profile_key
        if event_type.profile_mode == EventType.ProfileMode.EXTENSION
        else None
    )
    if not profile_key:
        return

    for event in events:
        if profile_key == PublicHealthEventProfile.expected_profile_key:
            profile, _ = PublicHealthEventProfile.objects.get_or_create(event=event)
            profile.insurance_eligible = profile_data.get("insurance_eligible", False)
            profile.referral_required = profile_data.get("referral_required", False)
            profile.save()
        elif profile_key == SportsEventProfile.expected_profile_key:
            profile, _ = SportsEventProfile.objects.get_or_create(event=event)
            profile.sport_name = profile_data.get("sport_name", "")
            profile.skill_level = profile_data.get("skill_level", "")
            profile.save()
        elif profile_key == CultureEventProfile.expected_profile_key:
            profile, _ = CultureEventProfile.objects.get_or_create(event=event)
            profile.format_label = profile_data.get("format_label", "")
            profile.age_rating = profile_data.get("age_rating", "")
            profile.save()


def persist_explicit_dates(
    series: EventSeries,
    explicit_dates: list[date],
) -> None:
    """Persist explicit dates for a manual-batch series.

    Replaces any existing dates. For non-manual-batch series, clears
    any stale date rows.
    """
    if series.series_mode != EventSeries.SeriesMode.MANUAL_BATCH:
        if series.pk:
            series.dates.all().delete()
        return

    series.dates.all().delete()
    EventSeriesDate.objects.bulk_create(
        [
            EventSeriesDate(
                series=series,
                occurrence_date=occurrence_date,
                display_order=index,
            )
            for index, occurrence_date in enumerate(explicit_dates, start=1)
        ]
    )
