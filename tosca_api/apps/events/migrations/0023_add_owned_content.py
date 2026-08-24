from django.db import migrations, models

import tosca_api.apps.core.editorjs


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0022_alter_event_visibility"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventseries",
            name="default_content",
            field=models.JSONField(
                blank=True,
                default=tosca_api.apps.core.editorjs.empty_document,
                help_text="Content inherited by occurrences without an override.",
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="content_override",
            field=models.JSONField(
                blank=True,
                default=None,
                help_text=(
                    "Occurrence-specific Editor.js content. Null inherits the series "
                    "default; an empty document explicitly suppresses inherited content."
                ),
                null=True,
            ),
        ),
    ]
