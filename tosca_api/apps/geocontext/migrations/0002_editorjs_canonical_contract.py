"""Switch GeoContext to canonical Editor.js JSON contract.

Replaces the legacy text ``content`` + ``content_type`` pair with a single
JSON-backed ``content`` column defaulting to ``{"blocks": []}``. Pre-production
destructive reset is acceptable for existing rows; no row-level backfill is
provided here (see Phase 7.4 for preflight/backfill tooling if needed).
"""

from django.db import migrations, models

from tosca_api.apps.geocontext.models import empty_editorjs_document


class Migration(migrations.Migration):

    dependencies = [
        ("geocontext", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="geocontext",
            name="content_type",
        ),
        migrations.RemoveField(
            model_name="geocontext",
            name="content",
        ),
        migrations.AddField(
            model_name="geocontext",
            name="content",
            field=models.JSONField(blank=True, default=empty_editorjs_document),
        ),
    ]
