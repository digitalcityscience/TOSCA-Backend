from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("geostories", "0016_geostoryresultpresentation"),
    ]

    operations = [
        migrations.RenameField(
            model_name="geostoryresultpresentation",
            old_name="title",
            new_name="caption",
        ),
        migrations.AlterField(
            model_name="geostoryresultpresentation",
            name="caption",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Optional caption for this result view; empty uses the simulation output title."
                ),
                max_length=255,
            ),
        ),
    ]
