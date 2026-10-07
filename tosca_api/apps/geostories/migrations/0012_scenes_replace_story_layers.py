from django.db import migrations

OVERVIEW_TITLE = "Overview"


def copy_story_layers_to_overview_scene(apps, schema_editor):
    """Give every story with legacy layers one camera-less "Overview" scene.

    A scene without a camera fits the map to its layers, which is exactly how
    v1 stories rendered, so readers see no change.
    """
    story_layer_model = apps.get_model("geostories", "GeoStoryLayer")
    scene_model = apps.get_model("geostories", "GeoStoryScene")
    scene_layer_model = apps.get_model("geostories", "GeoStorySceneLayer")

    story_ids = (
        story_layer_model.objects.order_by().values_list("geostory_id", flat=True).distinct()
    )
    for story_id in story_ids:
        if scene_model.objects.filter(geostory_id=story_id).exists():
            continue
        scene = scene_model.objects.create(geostory_id=story_id, order=0, title=OVERVIEW_TITLE)
        rows = story_layer_model.objects.filter(geostory_id=story_id).order_by(
            "display_order", "created_at"
        )
        scene_layer_model.objects.bulk_create(
            scene_layer_model(
                scene=scene,
                layer_id=row.layer_id,
                style_assignment_id=row.style_assignment_id,
                display_order=index,
            )
            for index, row in enumerate(rows)
        )


def remove_scenes(apps, schema_editor):
    # Before this migration GeoStoryLayer was the source of truth and it is
    # left untouched, so rolling back only needs to clear the scenes.
    apps.get_model("geostories", "GeoStoryScene").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("geostories", "0011_geostory_scenes"),
    ]

    operations = [
        migrations.RunPython(copy_story_layers_to_overview_scene, reverse_code=remove_scenes),
        migrations.RemoveField(
            model_name="geostory",
            name="layers",
        ),
    ]
