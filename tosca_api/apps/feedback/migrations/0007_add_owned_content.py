from django.db import migrations, models

import tosca_api.apps.core.editorjs


class Migration(migrations.Migration):
    dependencies = [
        ("feedback", "0006_alter_feedbacklayer_unique_together_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="geofeedback",
            name="content",
            field=models.JSONField(
                blank=True,
                default=tosca_api.apps.core.editorjs.empty_document,
                help_text="Main feedback content as a canonical Editor.js document.",
            ),
        ),
    ]
