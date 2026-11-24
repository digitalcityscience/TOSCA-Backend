"""Create the database cache table (``tosca_cache``) on every install that runs ``migrate``.

The default ``CACHES`` backend is ``DatabaseCache``; without its table every
throttled request fails. ``createcachetable`` is idempotent and does nothing
for other cache backends.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = [("core", "0005_backfill_media_asset_storage_alias")]

    operations = [migrations.RunPython(create_cache_table, migrations.RunPython.noop)]
