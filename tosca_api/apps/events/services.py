from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from .models import Event, EventSeries, EventTerm, TaxonomyTerm

KEEP_EXISTING_TERMS = object()

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
