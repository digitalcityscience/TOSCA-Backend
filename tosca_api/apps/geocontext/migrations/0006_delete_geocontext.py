from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("geocontext", "0005_copy_content_to_features"),
        ("geostories", "0008_remove_geostory_context"),
        ("feedback", "0008_remove_geofeedback_context"),
        ("events", "0024_remove_geocontext_fields"),
    ]

    operations = [
        migrations.DeleteModel(name="GeoContext"),
    ]
