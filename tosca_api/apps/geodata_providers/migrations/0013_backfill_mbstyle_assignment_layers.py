import json

from django.db import migrations


VECTOR_LAYER_TYPES = {"fill", "line", "symbol", "circle", "heatmap", "fill-extrusion"}


def backfill_mbstyle_assignment_layers(apps, schema_editor):
    assignment_model = apps.get_model("geodata_providers", "LayerStyleAssignment")
    assignments = assignment_model.objects.filter(
        style__format="mbstyle",
    ).select_related("style", "layer__workspace")

    for assignment in assignments.iterator():
        if assignment.style_layer_ids:
            continue
        try:
            payload = json.loads(assignment.style.file_content)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("layers"), list):
            continue

        style_layers = [
            style_layer
            for style_layer in payload["layers"]
            if isinstance(style_layer, dict)
            and style_layer.get("type") in VECTOR_LAYER_TYPES
            and isinstance(style_layer.get("id"), str)
            and style_layer["id"].strip()
        ]
        if not style_layers:
            continue

        target_names = {
            assignment.layer.name,
            f"{assignment.layer.workspace.name}:{assignment.layer.name}",
            f"{assignment.layer.workspace.name}/{assignment.layer.name}",
        }

        def references(style_layer):
            return {
                value
                for key in ("source-layer", "source")
                if isinstance((value := style_layer.get(key)), str) and value
            }

        selected_ids = [
            style_layer["id"]
            for style_layer in style_layers
            if references(style_layer) & target_names
            or any(
                reference.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
                == assignment.layer.name
                for reference in references(style_layer)
            )
        ]
        if not selected_ids:
            logical_sources = {
                (style_layer.get("source"), style_layer.get("source-layer"))
                for style_layer in style_layers
            }
            if len(logical_sources) <= 1:
                selected_ids = [style_layer["id"] for style_layer in style_layers]

        if selected_ids:
            assignment_model.objects.filter(pk=assignment.pk).update(
                style_layer_ids=selected_ids,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("geodata_providers", "0012_layergroupmember_render_configuration"),
    ]

    operations = [
        migrations.RunPython(
            backfill_mbstyle_assignment_layers,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
