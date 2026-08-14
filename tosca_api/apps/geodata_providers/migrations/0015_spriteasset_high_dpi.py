from django.db import migrations, models

import tosca_api.apps.geodata_providers.models


class Migration(migrations.Migration):
    dependencies = [
        ("geodata_providers", "0014_rich_descriptions"),
    ]

    operations = [
        migrations.AddField(
            model_name="spriteasset",
            name="image_2x",
            field=models.ImageField(
                blank=True,
                upload_to=tosca_api.apps.geodata_providers.models.sprite_image_2x_upload_to,
            ),
        ),
        migrations.AddField(
            model_name="spriteasset",
            name="index_content_2x",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
