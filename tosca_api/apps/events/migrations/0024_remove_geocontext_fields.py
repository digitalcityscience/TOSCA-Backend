from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0023_add_owned_content"),
        ("geocontext", "0005_copy_content_to_features"),
    ]

    operations = [
        migrations.RemoveField(model_name="event", name="context"),
        migrations.RemoveField(model_name="eventseries", name="default_context"),
    ]
