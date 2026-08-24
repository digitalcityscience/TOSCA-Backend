from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("geostories", "0007_add_owned_content"),
        ("geocontext", "0005_copy_content_to_features"),
    ]

    operations = [
        migrations.RemoveField(model_name="geostory", name="context"),
    ]
