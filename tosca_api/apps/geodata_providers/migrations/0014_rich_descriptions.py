import html
import re

from django.db import migrations, models

import tosca_api.apps.core.editorjs


def _document_from_text(value):
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return {"blocks": []}
    paragraphs = re.split(r"\n\s*\n+", text)
    return {
        "blocks": [
            {
                "type": "paragraph",
                "data": {
                    "text": "<br>".join(
                        html.escape(line, quote=False)
                        for line in paragraph.split("\n")
                    ),
                },
            }
            for paragraph in paragraphs
            if paragraph.strip()
        ],
    }


def preserve_existing_descriptions(apps, schema_editor):
    layer_model = apps.get_model("geodata_providers", "Layer")
    group_model = apps.get_model("geodata_providers", "LayerGroup")

    for layer in layer_model.objects.all().only("id", "description").iterator():
        layer_model.objects.filter(pk=layer.pk).update(
            description_content=_document_from_text(layer.description),
            provider_description=layer.description,
        )

    for group in group_model.objects.all().only("id", "description").iterator():
        group_model.objects.filter(pk=group.pk).update(
            description_content=_document_from_text(group.description),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("geodata_providers", "0013_backfill_mbstyle_assignment_layers"),
    ]

    operations = [
        migrations.AddField(
            model_name="layer",
            name="description_content",
            field=models.JSONField(
                blank=True,
                default=tosca_api.apps.core.editorjs.empty_document,
                help_text="Public rich description authored in TOSCA.",
            ),
        ),
        migrations.AddField(
            model_name="layer",
            name="provider_description",
            field=models.TextField(
                blank=True,
                editable=False,
                help_text=(
                    "Last description observed from the provider; never overwrites "
                    "authored content."
                ),
            ),
        ),
        migrations.AddField(
            model_name="layergroup",
            name="description_content",
            field=models.JSONField(
                blank=True,
                default=tosca_api.apps.core.editorjs.empty_document,
                help_text="Public rich description authored in TOSCA.",
            ),
        ),
        migrations.AlterField(
            model_name="layer",
            name="description",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Generated plain-text projection of the authored rich description."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="layergroup",
            name="description",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Generated plain-text projection of the authored rich description."
                ),
            ),
        ),
        migrations.RunPython(
            preserve_existing_descriptions,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
