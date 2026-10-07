from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('external_catalog', '0003_single_collection'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='categoryitem',
            name='ogc_collection_ids',
        ),
    ]
