"""
Add ``title`` field to GeoContext.

The field is optional (blank allowed, default ``""``) so existing rows
and test fixtures keep working, but the Django admin surfaces it
prominently so editors can label rich contexts that are referenced from
GeoStory / Event / GeoFeedback pickers. Blank rows fall back to a
derived excerpt via ``GeoContext.__str__``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("geocontext", "0002_editorjs_canonical_contract"),
    ]

    operations = [
        migrations.AddField(
            model_name="geocontext",
            name="title",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Human-readable label used in admin dropdowns where "
                    "GeoContext rows are referenced (GeoStory / Event / "
                    "GeoFeedback). Falls back to a derived excerpt when "
                    "left blank, but setting it explicitly keeps "
                    "related-object pickers usable."
                ),
                max_length=200,
            ),
        ),
    ]
