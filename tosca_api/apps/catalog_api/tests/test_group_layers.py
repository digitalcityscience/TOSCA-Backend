import io
import json
import tempfile

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.forms.models import inlineformset_factory
from django.urls import reverse
from PIL import Image

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerGroup,
    LayerGroupMember,
    LayerStyleAssignment,
    SpriteAsset,
    Store,
    Style,
    Workspace,
)
from tosca_api.apps.geodata_providers.admin import (
    LayerGroupMemberInlineForm,
    LayerGroupMemberInlineFormSet,
)


def sprite_png(*, size=(8, 8), color=(255, 0, 0, 255)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


class LayerGroupCatalogTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.media_override.enable()
        self.client = Client()
        self.user = User.objects.create_user(username="group-admin", password="test")
        self.provider = GeodataEngine.objects.create(
            name="Group provider",
            engine_type="geoserver",
            base_url="http://geoserver.internal/geoserver",
            public_url="https://maps.example.test/geoserver",
            is_active=True,
            is_default=True,
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.provider,
            name="mobility",
            created_by=self.user,
        )
        self.store = Store.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="mobility-store",
            store_type="postgis",
            host="db",
            database="gis",
            username="postgres",
            created_by=self.user,
        )
        self.layers = [
            Layer.objects.create(
                workspace=self.workspace,
                store=self.store,
                name=name,
                title=title,
                table_name=name,
                geometry_type=geometry,
                publishing_state="PUBLISHED",
                is_public=True,
                created_by=self.user,
            )
            for name, title, geometry in (
                ("roads", "Roads", "LineString"),
                ("stations", "Stations", "Point"),
            )
        ]
        self.style = Style.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="mobility-group-style",
            title="Mobility group",
            format="mbstyle",
            file_name="mobility-group-style.json",
            file_content=json.dumps(
                {
                    "version": 8,
                    "sources": {},
                    "layers": [
                        {
                            "id": "roads-line",
                            "type": "line",
                            "source": "roads",
                            "source-layer": "roads",
                            "paint": {"line-color": "#d84a2f", "line-width": 3},
                        },
                        {
                            "id": "station-circles",
                            "type": "circle",
                            "source": "stations",
                            "source-layer": "stations",
                            "paint": {"circle-color": "#154c79", "circle-radius": 5},
                        },
                    ],
                }
            ),
            validation_state="VALID",
            created_by=self.user,
        )
        self.assignments = [
            LayerStyleAssignment.objects.create(
                layer=layer,
                style=self.style,
                role="default",
                style_layer_ids=[style_layer_id],
                created_by=self.user,
            )
            for layer, style_layer_id in zip(
                self.layers,
                ("roads-line", "station-circles"),
                strict=True,
            )
        ]
        self.group = LayerGroup.objects.create(
            workspace=self.workspace,
            name="public-transport",
            title="Public transport",
            description="Roads and stations",
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )
        for order, (layer, assignment) in enumerate(zip(self.layers, self.assignments, strict=True)):
            LayerGroupMember.objects.create(
                group=self.group,
                layer=layer,
                style_assignment=assignment,
                order=order,
                source_alias=layer.name,
            )

    def tearDown(self):
        self.media_override.disable()
        self.media_dir.cleanup()

    def test_group_validates_ordered_styled_members(self):
        self.group.validate_members()
        self.assertEqual(
            list(self.group.members.values_list("layer__name", flat=True)),
            ["roads", "stations"],
        )

    def test_workspace_catalog_lists_layers_and_groups(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-layer-list",
                kwargs={
                    "provider_id": self.provider.id,
                    "workspace_name": self.workspace.name,
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [item["name"] for item in payload["layers"]["layer"]],
            ["roads", "stations"],
        )
        self.assertEqual(payload["groups"]["group"][0]["id"], str(self.group.id))
        self.assertEqual(payload["groups"]["group"][0]["member_count"], 2)
        self.assertEqual(payload["groups"]["group"][0]["composition"], "VECTOR")
        self.assertEqual(
            payload["groups"]["group"][0]["description_content"],
            self.group.description_content,
        )
        self.assertFalse(payload["groups"]["group"][0]["has_legend"])

    def test_group_manifest_exposes_canonical_sources_and_complete_style(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-group-detail",
                kwargs={
                    "provider_id": self.provider.id,
                    "workspace_name": self.workspace.name,
                    "group_name": self.group.name,
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        manifest = response.json()["group"]
        self.assertEqual(manifest["description"], "Roads and stations")
        self.assertEqual(manifest["description_content"], self.group.description_content)
        self.assertIsNone(manifest["legend"])
        self.assertEqual(set(manifest["sources"]), {"roads", "stations"})
        self.assertEqual(
            [layer["id"] for layer in manifest["layers"]],
            ["roads-line", "station-circles"],
        )
        self.assertIn("LAYER=mobility%3Aroads", manifest["sources"]["roads"]["tiles"][0])
        self.assertEqual(manifest["layers"][0]["source-layer"], "roads")
        self.assertTrue(manifest["styles"][str(self.style.id)]["href"].endswith(str(self.style.id)))
        self.assertEqual([member["name"] for member in manifest["members"]], ["roads", "stations"])
        self.assertEqual(
            manifest["members"][0]["style_assignment"]["style_layer_ids"],
            ["roads-line"],
        )

    def test_uploaded_group_legend_is_exposed_and_detects_composition_changes(self):
        self.group.legend_image = SimpleUploadedFile(
            "transport-legend.png",
            sprite_png(),
            content_type="image/png",
        )
        self.group.save()
        self.assertEqual(len(self.group.legend_content_hash), 64)
        self.assertFalse(self.group.legend_is_stale)

        detail_url = reverse(
            "catalog-v1-provider-workspace-group-detail",
            kwargs={
                "provider_id": self.provider.id,
                "workspace_name": self.workspace.name,
                "group_name": self.group.name,
            },
        )
        detail_response = self.client.get(detail_url)
        legend = detail_response.json()["group"]["legend"]
        self.assertFalse(legend["stale"])
        self.assertIn(self.group.legend_content_hash, legend["url"])

        image_response = self.client.get(legend["url"])
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response["Content-Type"], "image/png")
        self.assertEqual(image_response["ETag"], f'"{self.group.legend_content_hash}"')
        self.assertEqual(b"".join(image_response.streaming_content), sprite_png())

        first_member = self.group.members.order_by("order").first()
        LayerGroupMember.objects.filter(pk=first_member.pk).update(order=9)
        self.assertTrue(self.group.legend_is_stale)
        self.assertTrue(self.client.get(detail_url).json()["group"]["legend"]["stale"])

        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_login(self.user)
        members = self.group.members.order_by("order", "id")
        warning_response = self.client.post(
            reverse("admin:geodata_providers_layergroup_composition_warnings"),
            data=json.dumps({
                "group_id": str(self.group.id),
                "legend_will_refresh": False,
                "legend_will_be_removed": False,
                "members": [
                    {
                        "id": str(member.id),
                        "layer_id": str(member.layer_id),
                        "style_assignment_id": str(member.style_assignment_id),
                        "order": member.order,
                    }
                    for member in members
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(warning_response.status_code, 200)
        self.assertIn(
            "uploaded group legend",
            " ".join(warning_response.json()["warnings"]),
        )

        confirmed_payload = json.loads(warning_response.wsgi_request.body)
        confirmed_payload["legend_is_confirmed"] = True
        confirmed_response = self.client.post(
            reverse("admin:geodata_providers_layergroup_composition_warnings"),
            data=json.dumps(confirmed_payload),
            content_type="application/json",
        )
        self.assertEqual(confirmed_response.json()["warnings"], [])

        self.group.refresh_legend_composition_hash()
        self.assertFalse(self.group.legend_is_stale)

    def test_group_is_hidden_when_a_member_is_not_public(self):
        self.layers[0].is_public = False
        self.layers[0].save()
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-layer-list",
                kwargs={
                    "provider_id": self.provider.id,
                    "workspace_name": self.workspace.name,
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("groups", response.json())

    def test_raster_group_manifest_uses_one_wms_source_per_member(self):
        raster_store = Store.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="weather-store",
            store_type="geotiff",
            file_path="/data/weather",
            created_by=self.user,
        )
        raster_layers = [
            Layer.objects.create(
                workspace=self.workspace,
                store=raster_store,
                name=name,
                title=title,
                table_name=name,
                geometry_type="Polygon",
                publishing_state="PUBLISHED",
                is_public=True,
                created_by=self.user,
            )
            for name, title in (("rain", "Rainfall"), ("temperature", "Temperature"))
        ]
        raster_style = Style.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="weather-group-style",
            format="sld",
            file_name="weather-group-style.sld",
            file_content=(
                '<StyledLayerDescriptor version="1.0.0">'
                "<NamedLayer><Name>weather</Name></NamedLayer>"
                "</StyledLayerDescriptor>"
            ),
            validation_state="VALID",
            created_by=self.user,
        )
        raster_assignments = [
            LayerStyleAssignment.objects.create(
                layer=layer,
                style=raster_style,
                role="default",
                created_by=self.user,
            )
            for layer in raster_layers
        ]
        raster_group = LayerGroup.objects.create(
            workspace=self.workspace,
            name="weather",
            title="Weather",
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )
        for order, (layer, assignment) in enumerate(
            zip(raster_layers, raster_assignments, strict=True)
        ):
            LayerGroupMember.objects.create(
                group=raster_group,
                layer=layer,
                style_assignment=assignment,
                order=order,
            )

        raster_group.validate_members()
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-group-detail",
                kwargs={
                    "provider_id": self.provider.id,
                    "workspace_name": self.workspace.name,
                    "group_name": raster_group.name,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        manifest = response.json()["group"]
        self.assertEqual(set(manifest["sources"]), {"rain", "temperature"})
        self.assertEqual([layer["type"] for layer in manifest["layers"]], ["raster", "raster"])
        self.assertIn("STYLES=weather-group-style", manifest["sources"]["rain"]["tiles"][0])

    def test_group_can_mix_vector_and_raster_members_and_warn_about_order(self):
        raster_store = Store.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="imagery-store",
            store_type="geotiff",
            file_path="/data/imagery",
            created_by=self.user,
        )
        imagery = Layer.objects.create(
            workspace=self.workspace,
            store=raster_store,
            name="imagery",
            table_name="imagery",
            geometry_type="Polygon",
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )
        imagery_style = Style.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="imagery-style",
            format="sld",
            file_content=(
                '<StyledLayerDescriptor version="1.0.0">'
                '<NamedLayer><Name>imagery</Name></NamedLayer>'
                '</StyledLayerDescriptor>'
            ),
            validation_state="VALID",
            created_by=self.user,
        )
        imagery_assignment = LayerStyleAssignment.objects.create(
            layer=imagery,
            style=imagery_style,
            role="default",
            created_by=self.user,
        )
        mixed_group = LayerGroup.objects.create(
            workspace=self.workspace,
            name="mixed",
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )
        LayerGroupMember.objects.create(
            group=mixed_group,
            layer=self.layers[0],
            style_assignment=self.assignments[0],
            order=0,
        )
        LayerGroupMember.objects.create(
            group=mixed_group,
            layer=imagery,
            style_assignment=imagery_assignment,
            order=1,
        )

        mixed_group.validate_members()
        self.assertEqual(mixed_group.composition, "MIXED")
        self.assertIn("may obscure", mixed_group.publication_warnings()[0])

    def test_member_without_assignment_uses_layer_default(self):
        draft = LayerGroup.objects.create(
            workspace=self.workspace,
            name="defaulted",
            created_by=self.user,
        )
        member = LayerGroupMember.objects.create(
            group=draft,
            layer=self.layers[0],
            order=0,
        )
        self.assertEqual(member.style_assignment_id, self.assignments[0].id)

    def test_vector_member_rejects_sld_assignment(self):
        sld = Style.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="unsupported-vector-sld",
            format="sld",
            file_content=(
                '<StyledLayerDescriptor version="1.0.0">'
                '<NamedLayer><Name>roads</Name></NamedLayer>'
                '</StyledLayerDescriptor>'
            ),
            validation_state="VALID",
            created_by=self.user,
        )
        assignment = LayerStyleAssignment.objects.create(
            layer=self.layers[0],
            style=sld,
            role="alternate",
            created_by=self.user,
        )
        self.group.members.filter(layer=self.layers[0]).update(style_assignment=assignment)
        with self.assertRaisesMessage(ValidationError, "requires an MBSTYLE style"):
            self.group.validate_members()

    def test_same_layer_can_be_repeated_with_another_assignment(self):
        duplicate_group = LayerGroup.objects.create(
            workspace=self.workspace,
            name="repeated-layer",
            created_by=self.user,
        )
        LayerGroupMember.objects.create(
            group=duplicate_group,
            layer=self.layers[0],
            style_assignment=self.assignments[0],
            source_alias="roads-main",
            order=0,
        )
        duplicate = LayerGroupMember.objects.create(
            group=duplicate_group,
            layer=self.layers[0],
            style_assignment=self.assignments[0],
            source_alias="roads-overlay",
            order=1,
        )
        self.assertIsNotNone(duplicate.pk)

    def test_repeated_vector_layer_shares_source_and_supports_member_rule_overrides(self):
        overlay_style = Style.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="roads-dimensions",
            title="Road dimensions",
            format="mbstyle",
            file_content=json.dumps({
                "version": 8,
                "sources": {},
                "layers": [
                    {
                        "id": "roads-colors",
                        "type": "line",
                        "source": "roads",
                        "paint": {"line-color": "#d84a2f"},
                    },
                    {
                        "id": "roads-patterns",
                        "type": "line",
                        "source": "roads",
                        "paint": {"line-dasharray": [2, 2]},
                    },
                ],
            }),
            validation_state="VALID",
            created_by=self.user,
        )
        assignment = LayerStyleAssignment.objects.create(
            layer=self.layers[0],
            style=overlay_style,
            role="alternate",
            style_layer_ids=[],
            created_by=self.user,
        )
        repeated_group = LayerGroup.objects.create(
            workspace=self.workspace,
            name="roads-intersections",
            title="Road intersections",
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )
        first = LayerGroupMember.objects.create(
            group=repeated_group,
            layer=self.layers[0],
            style_assignment=assignment,
            title="Road colors",
            render_layer_ids=["roads-colors"],
            order=0,
        )
        second = LayerGroupMember.objects.create(
            group=repeated_group,
            layer=self.layers[0],
            style_assignment=assignment,
            title="Road patterns",
            render_layer_ids=["roads-patterns"],
            order=1,
        )

        repeated_group.validate_members()
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-group-detail",
                kwargs={
                    "provider_id": self.provider.id,
                    "workspace_name": self.workspace.name,
                    "group_name": repeated_group.name,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        manifest = response.json()["group"]
        self.assertEqual(len(manifest["sources"]), 1)
        self.assertNotEqual(first.source_alias, second.source_alias)
        self.assertEqual(
            {member["source_key"] for member in manifest["members"]},
            {first.source_alias},
        )
        self.assertEqual(
            [member["title"] for member in manifest["members"]],
            ["Road colors", "Road patterns"],
        )
        self.assertEqual(
            [member["effective_style_layer_ids"] for member in manifest["members"]],
            [["roads-colors"], ["roads-patterns"]],
        )
        self.assertEqual(
            [layer["id"] for layer in manifest["layers"]],
            ["roads-colors", "roads-patterns"],
        )
        self.assertEqual(
            {layer["source"] for layer in manifest["layers"]},
            {first.source_alias},
        )

    def test_new_member_auto_assigns_next_order_and_unique_source_alias(self):
        duplicate = LayerGroupMember.objects.create(
            group=self.group,
            layer=self.layers[1],
            style_assignment=self.assignments[1],
        )

        self.assertEqual(duplicate.order, 2)
        self.assertEqual(duplicate.source_alias, "stations-2")

    def test_inline_style_choices_show_styles_instead_of_assignment_relations(self):
        form = LayerGroupMemberInlineForm(instance=self.group.members.first())
        labels = [label for value, label in form.fields["style_assignment"].choices if value]

        self.assertIn("Mobility group", labels)
        self.assertTrue(all("->" not in label for label in labels))

    def test_inline_keeps_pinned_inactive_assignment_visible(self):
        member = self.group.members.first()
        member.style_assignment.is_active = False
        member.style_assignment.save(update_fields=["is_active", "updated_at"])

        form = LayerGroupMemberInlineForm(instance=member)
        choices = {
            str(value): label
            for value, label in form.fields["style_assignment"].choices
            if value
        }

        self.assertIn(str(member.style_assignment_id), choices)
        self.assertTrue(choices[str(member.style_assignment_id)].endswith("(inactive)"))

    def test_admin_save_persists_description_and_changed_member_style(self):
        alternate_assignment = LayerStyleAssignment.objects.create(
            layer=self.layers[0],
            style=self.style,
            role=LayerStyleAssignment.Role.ALTERNATE,
            style_layer_ids=["roads-line"],
            created_by=self.user,
        )
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_login(self.user)

        members = list(self.group.members.order_by("order", "id"))
        changed_member = next(member for member in members if member.layer_id == self.layers[0].id)
        description_content = {
            "blocks": [
                {
                    "type": "paragraph",
                    "data": {
                        "text": "<strong>Description</strong> and styles persist together."
                    },
                }
            ]
        }
        payload = {
            "workspace": str(self.workspace.id),
            "name": self.group.name,
            "title": self.group.title,
            "description_content": json.dumps(description_content),
            "publishing_state": self.group.publishing_state,
            "is_public": "on",
            "members-TOTAL_FORMS": str(len(members)),
            "members-INITIAL_FORMS": str(len(members)),
            "members-MIN_NUM_FORMS": "0",
            "members-MAX_NUM_FORMS": "1000",
            "_save": "Save",
        }
        for index, member in enumerate(members):
            assignment_id = (
                alternate_assignment.id
                if member.id == changed_member.id
                else member.style_assignment_id
            )
            target_order = len(members) - 1 - member.order
            payload.update({
                f"members-{index}-id": str(member.id),
                f"members-{index}-title": member.title,
                f"members-{index}-layer": str(member.layer_id),
                f"members-{index}-style_assignment": str(assignment_id),
                f"members-{index}-render_layer_ids": json.dumps(member.render_layer_ids),
                f"members-{index}-order": str(target_order),
            })

        change_url = reverse(
            "admin:geodata_providers_layergroup_change",
            args=[self.group.id],
        )
        response = self.client.post(change_url, payload)

        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        changed_member.refresh_from_db()
        self.assertEqual(self.group.description_content, description_content)
        self.assertEqual(
            self.group.description,
            "Description and styles persist together.",
        )
        self.assertEqual(changed_member.style_assignment_id, alternate_assignment.id)
        self.assertEqual(changed_member.order, len(members) - 1)

        reload_response = self.client.get(change_url)
        self.assertEqual(reload_response.status_code, 200)
        reloaded_group = reload_response.context["adminform"].form.instance
        inline_forms = reload_response.context["inline_admin_formsets"][0].formset.initial_forms
        reloaded_members = {form.instance.id: form for form in inline_forms}
        self.assertEqual(reloaded_group.description_content, description_content)
        self.assertEqual(
            str(reloaded_members[changed_member.id]["style_assignment"].value()),
            str(alternate_assignment.id),
        )

    def test_inline_can_reorder_existing_members_without_transient_duplicates(self):
        third = LayerGroupMember.objects.create(
            group=self.group,
            layer=self.layers[0],
            style_assignment=self.assignments[0],
        )
        members = list(self.group.members.order_by("order"))
        FormSet = inlineformset_factory(
            LayerGroup,
            LayerGroupMember,
            form=LayerGroupMemberInlineForm,
            formset=LayerGroupMemberInlineFormSet,
            fields=("layer", "style_assignment", "order"),
            extra=0,
        )
        # Swap two rows while a third row remains unchanged. The unchanged row
        # must also be restored after the temporary collision-free update.
        target_orders = [1, 0, 2]
        data = {
            "members-TOTAL_FORMS": "3",
            "members-INITIAL_FORMS": "3",
            "members-MIN_NUM_FORMS": "0",
            "members-MAX_NUM_FORMS": "1000",
        }
        for index, (member, target_order) in enumerate(zip(members, target_orders)):
            data.update({
                f"members-{index}-id": str(member.id),
                f"members-{index}-layer": str(member.layer_id),
                f"members-{index}-style_assignment": str(member.style_assignment_id),
                f"members-{index}-order": str(target_order),
            })

        formset = FormSet(data=data, instance=self.group, prefix="members")
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        saved_orders = dict(
            self.group.members.values_list("id", "order")
        )
        self.assertEqual(
            saved_orders,
            {member.id: target_order for member, target_order in zip(members, target_orders)},
        )
        self.assertEqual(third.source_alias, "roads-2")

        duplicate_data = data.copy()
        duplicate_data["members-0-order"] = "0"
        duplicate_data["members-1-order"] = "0"
        duplicate_data["members-2-order"] = "2"
        duplicate_formset = FormSet(
            data=duplicate_data,
            instance=self.group,
            prefix="members",
        )
        self.assertFalse(duplicate_formset.is_valid())
        self.assertIn("unique order", str(duplicate_formset.non_form_errors()))

    def test_inline_readded_raster_gets_derived_values_and_order_warning(self):
        raster_store = Store.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="inline-imagery-store",
            store_type="geotiff",
            file_path="/data/inline-imagery",
            created_by=self.user,
        )
        raster_layer = Layer.objects.create(
            workspace=self.workspace,
            store=raster_store,
            name="inline-imagery",
            table_name="inline-imagery",
            geometry_type="Polygon",
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )
        raster_style = Style.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="inline-imagery-style",
            title="Inline imagery",
            format="sld",
            file_content=(
                '<StyledLayerDescriptor version="1.0.0">'
                '<NamedLayer><Name>inline-imagery</Name></NamedLayer>'
                '</StyledLayerDescriptor>'
            ),
            validation_state="VALID",
            created_by=self.user,
        )
        raster_assignment = LayerStyleAssignment.objects.create(
            layer=raster_layer,
            style=raster_style,
            role="default",
            created_by=self.user,
        )
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_login(self.user)
        warning_response = self.client.post(
            reverse("admin:geodata_providers_layergroup_composition_warnings"),
            data=json.dumps({
                "members": [
                    {"layer_id": str(self.layers[0].id), "order": 0},
                    {"layer_id": str(self.layers[1].id), "order": 1},
                    {"layer_id": str(raster_layer.id), "order": 2},
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(warning_response.status_code, 200)
        self.assertIn("may obscure", warning_response.json()["warnings"][0])

        members = list(self.group.members.order_by("order"))
        FormSet = inlineformset_factory(
            LayerGroup,
            LayerGroupMember,
            form=LayerGroupMemberInlineForm,
            formset=LayerGroupMemberInlineFormSet,
            fields=("layer", "style_assignment", "order"),
            extra=0,
        )
        data = {
            "members-TOTAL_FORMS": "5",
            "members-INITIAL_FORMS": "2",
            "members-MIN_NUM_FORMS": "0",
            "members-MAX_NUM_FORMS": "1000",
        }
        for index, member in enumerate(members):
            data.update({
                f"members-{index}-id": str(member.id),
                f"members-{index}-layer": str(member.layer_id),
                f"members-{index}-style_assignment": str(member.style_assignment_id),
                f"members-{index}-order": str(member.order),
            })
        data.update({
            "members-2-layer": str(raster_layer.id),
            "members-2-style_assignment": "",
            "members-2-order": "0",
            # Suggested order values on untouched extra rows must not make those
            # rows validate as partially completed members.
            "members-3-layer": "",
            "members-3-style_assignment": "",
            "members-3-order": "3",
            "members-4-layer": "",
            "members-4-style_assignment": "",
            "members-4-order": "4",
        })

        formset = FormSet(data=data, instance=self.group, prefix="members")
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        added = self.group.members.get(layer=raster_layer)
        self.assertEqual(added.order, 2)
        self.assertEqual(added.source_alias, "inline-imagery")
        self.assertEqual(added.style_assignment_id, raster_assignment.id)
        self.assertIn("may obscure", self.group.publication_warnings()[0])


class SpriteAssetCatalogTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.media_dir.name, MEDIA_URL="/media/")
        self.override.enable()
        self.user = User.objects.create_user(username="sprite-admin", password="test")
        self.provider = GeodataEngine.objects.create(
            name="Sprite provider",
            engine_type="geoserver",
            base_url="http://geoserver.internal/geoserver",
            public_url="https://maps.example.test/geoserver",
            is_active=True,
            created_by=self.user,
        )

    def tearDown(self):
        self.override.disable()
        self.media_dir.cleanup()

    def test_sprite_pair_is_validated_and_served_with_cache_headers(self):
        image_2x = sprite_png(size=(16, 16), color=(0, 0, 255, 255))
        sprite = SpriteAsset.objects.create(
            geodata_engine=self.provider,
            name="transport-icons",
            image=SimpleUploadedFile("sprite.png", sprite_png(), content_type="image/png"),
            index_content={
                "station": {"width": 8, "height": 8, "x": 0, "y": 0, "pixelRatio": 1}
            },
            image_2x=SimpleUploadedFile("sprite@2x.png", image_2x, content_type="image/png"),
            index_content_2x={
                "station": {
                    "width": 16,
                    "height": 16,
                    "x": 0,
                    "y": 0,
                    "pixelRatio": 2,
                }
            },
            created_by=self.user,
        )
        compatibility_response = Client().get(
            reverse(
                "catalog-v1-provider-sprite-json",
                kwargs={"provider_id": self.provider.id, "sprite_id": sprite.id},
            )
        )
        self.assertEqual(compatibility_response.status_code, 302)
        self.assertEqual(compatibility_response["Cache-Control"], "no-cache")

        response = Client().get(compatibility_response["Location"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["station"]["width"], 8)
        self.assertEqual(response["ETag"], f'"{sprite.content_hash}"')
        self.assertIn("immutable", response["Cache-Control"])

        image_response = Client().get(
            reverse(
                "catalog-v1-provider-sprite-png-2x-versioned",
                kwargs={
                    "provider_id": self.provider.id,
                    "sprite_id": sprite.id,
                    "content_hash": sprite.content_hash,
                },
            )
        )
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response["Content-Type"], "image/png")
        self.assertEqual(b"".join(image_response.streaming_content), image_2x)

        index_response = Client().get(
            reverse(
                "catalog-v1-provider-sprite-json-2x-versioned",
                kwargs={
                    "provider_id": self.provider.id,
                    "sprite_id": sprite.id,
                    "content_hash": sprite.content_hash,
                },
            )
        )
        self.assertEqual(index_response.json()["station"]["pixelRatio"], 2)

        stale_response = Client().get(
            reverse(
                "catalog-v1-provider-sprite-json-versioned",
                kwargs={
                    "provider_id": self.provider.id,
                    "sprite_id": sprite.id,
                    "content_hash": "0" * 64,
                },
            )
        )
        self.assertEqual(stale_response.status_code, 404)

    def test_high_dpi_route_falls_back_to_1x_pair(self):
        sprite = SpriteAsset.objects.create(
            geodata_engine=self.provider,
            name="fallback-icons",
            image=SimpleUploadedFile("sprite.png", sprite_png(), content_type="image/png"),
            index_content={
                "station": {"width": 8, "height": 8, "x": 0, "y": 0, "pixelRatio": 1}
            },
            created_by=self.user,
        )
        response = Client().get(
            reverse(
                "catalog-v1-provider-sprite-json-2x-versioned",
                kwargs={
                    "provider_id": self.provider.id,
                    "sprite_id": sprite.id,
                    "content_hash": sprite.content_hash,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["station"]["pixelRatio"], 1)

    def test_sprite_names_cannot_conflict_with_maplibre_namespace_separator(self):
        with self.assertRaisesMessage(ValidationError, "cannot contain ':'"):
            SpriteAsset.objects.create(
                geodata_engine=self.provider,
                name="invalid-icons",
                image=SimpleUploadedFile("sprite.png", sprite_png(), content_type="image/png"),
                index_content={
                    "transport:station": {
                        "width": 8,
                        "height": 8,
                        "x": 0,
                        "y": 0,
                        "pixelRatio": 1,
                    }
                },
                created_by=self.user,
            )

    def test_mbstyle_requires_an_attached_sprite_for_icons(self):
        with self.assertRaisesMessage(ValidationError, "requires a sprite asset"):
            Style.objects.create(
                geodata_engine=self.provider,
                name="missing-sprite-style",
                format="mbstyle",
                file_content=json.dumps({
                    "version": 8,
                    "layers": [{
                        "id": "station-icons",
                        "type": "symbol",
                        "source": "stations",
                        "layout": {"icon-image": "station"},
                    }],
                }),
                created_by=self.user,
            )

    def test_mbstyle_sprite_references_must_exist_in_attached_index(self):
        sprite = SpriteAsset.objects.create(
            geodata_engine=self.provider,
            name="transport-icons",
            image=SimpleUploadedFile("sprite.png", sprite_png(), content_type="image/png"),
            index_content={
                "station": {"width": 8, "height": 8, "x": 0, "y": 0, "pixelRatio": 1}
            },
            created_by=self.user,
        )

        with self.assertRaisesMessage(ValidationError, "missing MBStyle images: bus"):
            Style.objects.create(
                geodata_engine=self.provider,
                name="unknown-icon-style",
                format="mbstyle",
                file_content=json.dumps({
                    "version": 8,
                    "layers": [{
                        "id": "bus-icons",
                        "type": "symbol",
                        "source": "stations",
                        "layout": {"icon-image": "bus"},
                    }],
                }),
                sprite_asset=sprite,
                created_by=self.user,
            )

    def test_mbstyle_response_uses_content_hashed_sprite_stem(self):
        sprite = SpriteAsset.objects.create(
            geodata_engine=self.provider,
            name="versioned-transport-icons",
            image=SimpleUploadedFile("sprite.png", sprite_png(), content_type="image/png"),
            index_content={
                "station": {"width": 8, "height": 8, "x": 0, "y": 0, "pixelRatio": 1}
            },
            created_by=self.user,
        )
        style = Style.objects.create(
            geodata_engine=self.provider,
            name="station-icon-style",
            format="mbstyle",
            file_content=json.dumps({
                "version": 8,
                "layers": [{
                    "id": "station-icons",
                    "type": "symbol",
                    "source": "stations",
                    "layout": {"icon-image": "station"},
                }],
            }),
            sprite_asset=sprite,
            validation_state=Style.ValidationState.VALID,
            created_by=self.user,
        )

        response = Client().get(
            reverse(
                "catalog-v1-provider-style-detail",
                kwargs={"provider_id": self.provider.id, "style_ref": style.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.json()["sprite"].endswith(f"/{sprite.id}/{sprite.content_hash}")
        )
