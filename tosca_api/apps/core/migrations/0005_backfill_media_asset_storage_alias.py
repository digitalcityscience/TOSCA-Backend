"""Backfill MediaAsset.storage_alias for rows created before the field existed.

Epic-11 PR3 adds ``storage_alias`` so the lifecycle service knows which
bucket an object currently lives in without a storage round-trip. Existing
rows default to ``StorageAlias.DEFAULT`` (see migration 0004), which is
correct for private-bucket uploads but wrong for anything already routed to
the public bucket (EditorJS inline uploads). This backfill reuses the same
prefix-routing rule ``migrate_media_paths`` already established as the
source of truth for "which alias does this legacy path belong in" so the
two stay in lockstep.
"""

from django.db import migrations

# Mirrors core.media_migration.DEFAULT_PUBLIC_PREFIXES / the routing rule in
# management/commands/migrate_media_paths.py::_alias_for_asset. Duplicated
# (rather than imported) because data migrations must not depend on
# application code that may change shape after this migration is frozen.
_PUBLIC_PREFIXES = ("geocontext/editorjs/",)


def backfill_storage_alias(apps, schema_editor):
    MediaAsset = apps.get_model("core", "MediaAsset")
    public_q = None
    from django.db.models import Q

    for prefix in _PUBLIC_PREFIXES:
        clause = Q(storage_path__startswith=prefix)
        public_q = clause if public_q is None else public_q | clause
    if public_q is not None:
        MediaAsset.objects.filter(public_q).update(storage_alias="media_public")


def noop_reverse(apps, schema_editor):
    # Deliberately not reversible -- matches the sibling ownership backfill
    # migration's rationale: unwinding would discard real state a later
    # migration or the lifecycle sync may have layered on top.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_mediaasset_storage_alias"),
    ]

    operations = [
        migrations.RunPython(backfill_storage_alias, noop_reverse),
    ]
