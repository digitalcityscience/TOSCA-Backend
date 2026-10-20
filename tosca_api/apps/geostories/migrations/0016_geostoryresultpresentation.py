import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("geosimulations", "0008_started_comparisons"),
        ("geostories", "0015_geostoryscenesimulation_display_order"),
    ]

    operations = [
        migrations.CreateModel(
            name="GeoStoryResultPresentation",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Optional story-specific title; empty uses the simulation output title.",
                        max_length=255,
                    ),
                ),
                (
                    "view_type",
                    models.CharField(
                        choices=[
                            ("table", "Table"),
                            ("metric_grid", "Metric grid"),
                            ("bar_chart", "Bar chart"),
                            ("line_chart", "Line chart"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "configuration",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text=(
                            "View-specific v1 configuration. Tables and metric grids may use {}; "
                            "charts reference dataset fields."
                        ),
                    ),
                ),
                (
                    "output",
                    models.ForeignKey(
                        help_text=(
                            "An exact published table output; the reference is protected for reproducibility."
                        ),
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="geostory_presentations",
                        to="geosimulations.simulationoutput",
                    ),
                ),
                (
                    "story",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="result_presentations",
                        to="geostories.geostory",
                    ),
                ),
            ],
            options={
                "verbose_name": "GeoStory simulation result",
                "verbose_name_plural": "GeoStory simulation results",
                "ordering": ["created_at"],
            },
        ),
    ]
