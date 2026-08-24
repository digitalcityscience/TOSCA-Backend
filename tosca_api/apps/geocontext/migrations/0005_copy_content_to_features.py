"""Copy shared GeoContext documents into their owning feature records."""

import copy

from django.db import migrations


EMPTY_DOCUMENT = {"blocks": []}


BATCH_SIZE = 1000


def _bulk_copy(queryset, *, context_field, content_field, documents):
    rows = list(queryset.only("id", context_field).iterator())
    for row in rows:
        context_id = getattr(row, context_field)
        setattr(row, content_field, copy.deepcopy(documents.get(context_id) or EMPTY_DOCUMENT))
    for start in range(0, len(rows), BATCH_SIZE):
        queryset.model.objects.bulk_update(
            rows[start : start + BATCH_SIZE], [content_field], batch_size=BATCH_SIZE
        )


def copy_content_to_features(apps, schema_editor):
    GeoContext = apps.get_model("geocontext", "GeoContext")
    GeoStory = apps.get_model("geostories", "GeoStory")
    GeoFeedback = apps.get_model("feedback", "GeoFeedback")
    Event = apps.get_model("events", "Event")
    EventSeries = apps.get_model("events", "EventSeries")

    documents = dict(GeoContext.objects.values_list("id", "content").iterator())

    _bulk_copy(
        GeoStory.objects.exclude(context_id=None),
        context_field="context_id",
        content_field="content",
        documents=documents,
    )
    _bulk_copy(
        GeoFeedback.objects.exclude(context_id=None),
        context_field="context_id",
        content_field="content",
        documents=documents,
    )
    _bulk_copy(
        Event.objects.exclude(context_id=None),
        context_field="context_id",
        content_field="content_override",
        documents=documents,
    )
    _bulk_copy(
        EventSeries.objects.exclude(default_context_id=None),
        context_field="default_context_id",
        content_field="default_content",
        documents=documents,
    )


def _bulk_restore(rows, *, GeoContext, fk_field, title_fn, content_fn, creator_id_fn):
    contexts = [
        GeoContext(
            title=title_fn(row),
            content=copy.deepcopy(content_fn(row) or EMPTY_DOCUMENT),
            created_by_id=creator_id_fn(row),
        )
        for row in rows
    ]
    created = GeoContext.objects.bulk_create(contexts, batch_size=BATCH_SIZE)
    for row, context in zip(rows, created):
        setattr(row, fk_field, context.pk)
    for start in range(0, len(rows), BATCH_SIZE):
        type(rows[0]).objects.bulk_update(
            rows[start : start + BATCH_SIZE], [fk_field], batch_size=BATCH_SIZE
        )


def restore_geocontexts(apps, schema_editor):
    """Create one rollback GeoContext per feature and restore its FK."""
    GeoContext = apps.get_model("geocontext", "GeoContext")
    GeoStory = apps.get_model("geostories", "GeoStory")
    GeoFeedback = apps.get_model("feedback", "GeoFeedback")
    Event = apps.get_model("events", "Event")
    EventSeries = apps.get_model("events", "EventSeries")

    stories = list(GeoStory.objects.select_related("campaign").iterator())
    if stories:
        _bulk_restore(
            stories,
            GeoContext=GeoContext,
            fk_field="context_id",
            title_fn=lambda s: s.title,
            content_fn=lambda s: s.content,
            creator_id_fn=lambda s: s.author_id,
        )

    feedbacks = list(GeoFeedback.objects.select_related("campaign").iterator())
    if feedbacks:
        _bulk_restore(
            feedbacks,
            GeoContext=GeoContext,
            fk_field="context_id",
            title_fn=lambda f: f.title,
            content_fn=lambda f: f.content,
            creator_id_fn=lambda f: f.created_by_id,
        )

    events = list(Event.objects.exclude(content_override=None).iterator())
    if events:
        _bulk_restore(
            events,
            GeoContext=GeoContext,
            fk_field="context_id",
            title_fn=lambda e: e.title,
            content_fn=lambda e: e.content_override,
            creator_id_fn=lambda e: e.organizer_id,
        )

    series_list = list(EventSeries.objects.select_related("campaign").iterator())
    if series_list:
        _bulk_restore(
            series_list,
            GeoContext=GeoContext,
            fk_field="default_context_id",
            title_fn=lambda s: s.name,
            content_fn=lambda s: s.default_content,
            creator_id_fn=lambda s: s.created_by_id or s.campaign.created_by_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("geocontext", "0004_alter_geocontext_id"),
        ("geostories", "0007_add_owned_content"),
        ("feedback", "0007_add_owned_content"),
        ("events", "0023_add_owned_content"),
    ]

    operations = [
        migrations.RunPython(copy_content_to_features, restore_geocontexts),
    ]
