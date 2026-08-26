from django.db import migrations, models
import django.db.models.deletion


def pin_existing_default_styles(apps, schema_editor):
    story_layer_model = apps.get_model("geostories", "GeoStoryLayer")
    assignment_model = apps.get_model("geodata_providers", "LayerStyleAssignment")

    defaults = {
        assignment.layer_id: assignment.id
        for assignment in assignment_model.objects.filter(role="default", is_active=True).only(
            "id", "layer_id"
        )
    }
    for layer_id, assignment_id in defaults.items():
        story_layer_model.objects.filter(
            layer_id=layer_id, style_assignment_id__isnull=True
        ).update(style_assignment_id=assignment_id)


class Migration(migrations.Migration):
    dependencies = [
        ("geodata_providers", "0017_geodataengine_organizations"),
        ("geostories", "0008_remove_geostory_context"),
    ]

    operations = [
        migrations.AddField(
            model_name="geostorylayer",
            name="style_assignment",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Pinned style assignment; defaults to the layer's active default assignment."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="geostory_uses",
                to="geodata_providers.layerstyleassignment",
            ),
        ),
        migrations.RunPython(
            pin_existing_default_styles,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
