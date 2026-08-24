from django.db import migrations, models

import tosca_api.apps.core.editorjs


class Migration(migrations.Migration):
    dependencies = [
        ("geostories", "0006_geostory_hero_image_storage_alias_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="geostory",
            name="content",
            field=models.JSONField(
                blank=True,
                default=tosca_api.apps.core.editorjs.empty_document,
                help_text="The story body as a canonical Editor.js document.",
            ),
        ),
    ]
