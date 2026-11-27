"""Write tools: layers, styles, layer groups and geostories (needs the database).

GeoServer is not called: the engine client and the publish services are patched where
a test would reach it. What is checked is the MCP side: which records a caller may
reach, which service is called with what, and what the assistant gets back.
"""

from unittest.mock import MagicMock, patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.test import TestCase
from mcp.server.mcpserver.exceptions import ToolError

from tosca_api.apps.authentication.identity import KEYCLOAK_PROVIDER
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerGroup,
    LayerStyleAssignment,
    Store,
    Style,
    Workspace,
)
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.mcp_server.authorization import django_user
from tosca_api.apps.mcp_server.principal import McpPrincipal
from tosca_api.apps.mcp_server.tools import geostories as story_tools
from tosca_api.apps.mcp_server.tools import publishing
from tosca_api.apps.organizations.models import Organization

SLD = '<StyledLayerDescriptor><NamedLayer><Name>{}</Name></NamedLayer></StyledLayerDescriptor>'
OWN = McpPrincipal(sub="sub-own", username="own-reader", org_slug="own", level="READER", exempt=False)
OTHER = McpPrincipal(sub="sub-other", username="other-reader", org_slug="other", level="READER", exempt=False)
ADMIN = McpPrincipal(sub="sub-admin", username="root", org_slug=None, level=None, exempt=True)
NOBODY = McpPrincipal(sub="sub-unknown", username="new", org_slug="own", level="READER", exempt=False)


class WriteToolTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create(username="own-reader")
        for principal in (OWN, OTHER, ADMIN):
            user = cls.user if principal is OWN else User.objects.create(username=principal.username)
            SocialAccount.objects.create(user=user, provider=KEYCLOAK_PROVIDER, uid=principal.sub)
        cls.own = Organization.objects.create(name="Own", slug="own")
        cls.other = Organization.objects.create(name="Other", slug="other")
        cls.engine = GeodataEngine.objects.create(
            name="Engine", engine_type="geoserver", base_url="http://geoserver.internal/geoserver",
            public_url="https://maps.example/geoserver",
            admin_username="admin", admin_password="secret", is_active=True, is_default=True, created_by=cls.user,
        )
        cls.provider_id = str(cls.engine.id)
        cls.workspace = Workspace.objects.create(
            geodata_engine=cls.engine, name="own_ws", visibility="public", organization=cls.own, created_by=cls.user,
        )
        cls.other_workspace = Workspace.objects.create(
            geodata_engine=cls.engine, name="other_ws", visibility="public", organization=cls.other,
            created_by=cls.user,
        )
        cls.store = cls._store(cls.workspace, "own_store")
        other_store = cls._store(cls.other_workspace, "other_store")
        cls.parks = cls._layer(cls.workspace, cls.store, "parks", "PUBLISHED")
        cls.draft = cls._layer(cls.workspace, cls.store, "draft_trees", "DRAFT")
        cls.foreign = cls._layer(cls.other_workspace, other_store, "foreign", "PUBLISHED")
        cls.green = cls._style(cls.workspace, "green")
        cls.red = cls._style(cls.workspace, "red")
        cls.global_style = cls._style(None, "shared")
        cls.foreign_style = cls._style(cls.other_workspace, "theirs")
        LayerStyleAssignment.objects.create(layer=cls.parks, style=cls.green, role="default", created_by=cls.user)
        cls.campaign = Campaign.objects.create(title="Own campaign", created_by=cls.user, organization=cls.own)
        cls.other_campaign = Campaign.objects.create(title="Their campaign", created_by=cls.user, organization=cls.other)

    @classmethod
    def _store(cls, workspace, name):
        return Store.objects.create(
            workspace=workspace, geodata_engine=cls.engine, name=name, store_type="postgis", host="db.internal",
            port=5432, database="gis", username="postgres", password="secret", schema="public", created_by=cls.user,
        )

    @classmethod
    def _layer(cls, workspace, store, name, state):
        return Layer.objects.create(
            workspace=workspace, store=store, name=name, title=name.title(), table_name=f"t_{name}",
            geometry_column="geom", geometry_type="Polygon", srid=4326, publishing_state=state, is_public=True,
            created_by=cls.user,
        )

    @classmethod
    def _style(cls, workspace, name):
        return Style.objects.create(
            geodata_engine=cls.engine, workspace=workspace, name=name, format="sld", file_name=f"{name}.sld",
            file_content=SLD.format(name), validation_state="VALID", remote_state="SYNCED", created_by=cls.user,
        )


class AccountTests(WriteToolTestCase):
    def test_writes_need_a_django_account(self):
        self.assertEqual(django_user(OWN), self.user)
        with self.assertRaises(ToolError) as raised:
            django_user(NOBODY)
        self.assertIn("Sign in to the TOSCA web app once", str(raised.exception))


class LayerToolTests(WriteToolTestCase):
    def test_lists_own_layers_in_every_state(self):
        names = {item["name"] for item in publishing.list_editable_layers_for(OWN)["layers"]}
        self.assertEqual(names, {"parks", "draft_trees"})
        drafts = publishing.list_editable_layers_for(OWN, state="DRAFT")["layers"]
        self.assertEqual([item["name"] for item in drafts], ["draft_trees"])
        self.assertNotIn("show_on_map", drafts[0])
        self.assertEqual(publishing.list_editable_layers_for(ADMIN)["total"], 3)

    def test_another_organizations_layer_is_not_found(self):
        target = {"provider_id": self.provider_id, "workspace": "other_ws", "name": "foreign"}
        calls = {
            "unpublish": lambda: publishing.unpublish_layer_for(OWN, **target),
            "update": lambda: publishing.update_layer_for(OWN, **target, title="x"),
            "default style": lambda: publishing.set_layer_default_style_for(OWN, **target, style="green"),
        }
        for name, call in calls.items():
            with self.subTest(tool=name):
                with self.assertRaises(ToolError) as raised:
                    call()
                self.assertIn("not found", str(raised.exception))

    @patch.object(publishing.LayerService, "publish_existing_layer", return_value={"success": True})
    def test_publish_an_existing_draft(self, publish):
        result = publishing.publish_layer_for(OWN, provider_id=self.provider_id, workspace="own_ws", name="draft_trees")
        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[0], self.draft)
        self.assertTrue(result["published"])

    @patch.object(publishing, "_sync_workspace", return_value=[])
    @patch.object(publishing.LayerService, "publish_postgis")
    def test_publish_under_a_new_name_reuses_the_layers_table(self, publish, _sync):
        def create(**kwargs):
            layer = Layer.objects.create(
                workspace=kwargs["workspace"], store=kwargs["store"], name=kwargs["layer_name"],
                title=kwargs["title"], table_name=kwargs["table_name"], geometry_column="geom",
                geometry_type="Polygon", srid=4326, publishing_state="PUBLISHED", is_public=True,
                created_by=kwargs["user"],
            )
            return {"success": True, "created": True, "resource": layer}

        publish.side_effect = create
        result = publishing.publish_layer_for(
            OWN, provider_id=self.provider_id, workspace="own_ws", name="parks_2026", from_layer="parks",
        )
        kwargs = publish.call_args.kwargs
        self.assertEqual((kwargs["table_name"], kwargs["store"], kwargs["layer_name"]), ("t_parks", self.store, "parks_2026"))
        self.assertEqual((kwargs["geometry_type"], kwargs["srid"], kwargs["user"]), ("Polygon", 4326, self.user))
        self.assertEqual(result["layer"]["show_on_map"]["arguments"]["layerName"], "parks_2026")

    def test_publish_refuses_unknown_names_and_taken_names(self):
        with self.assertRaises(ToolError) as raised:
            publishing.publish_layer_for(OWN, provider_id=self.provider_id, workspace="own_ws", name="nothing")
        self.assertIn("from_layer", str(raised.exception))
        with self.assertRaises(ToolError) as raised:
            publishing.publish_layer_for(
                OWN, provider_id=self.provider_id, workspace="own_ws", name="parks", from_layer="draft_trees",
            )
        self.assertIn("already exists", str(raised.exception))
        with self.assertRaises(ToolError):
            publishing.publish_layer_for(OTHER, provider_id=self.provider_id, workspace="own_ws", name="draft_trees")

    @patch.object(publishing, "get_geometry_tables", return_value=[
        {"table_name": "t_parks", "geometry_column": "geom", "geometry_type": "Polygon", "srid": 4326},
        {"table_name": "benches", "geometry_column": "the_geom", "geometry_type": "Point", "srid": 25832},
    ])
    def test_publishable_tables_mark_used_ones(self, _tables):
        result = publishing.list_publishable_tables_for(OWN, provider_id=self.provider_id, workspace="own_ws")
        tables = {item["table"]: item["has_layer"] for item in result["stores"][0]["tables"]}
        self.assertEqual(tables, {"t_parks": True, "benches": False})
        self.assertNotIn("db.internal", str(result))

    @patch.object(publishing.LayerService, "unpublish_layer", return_value={"success": True})
    def test_unpublish(self, unpublish):
        publishing.unpublish_layer_for(OWN, provider_id=self.provider_id, workspace="own_ws", name="parks")
        self.assertEqual(unpublish.call_args.args[0], self.parks)

    def test_update_title_of_an_unpublished_layer(self):
        result = publishing.update_layer_for(
            OWN, provider_id=self.provider_id, workspace="own_ws", name="draft_trees", title="Street trees",
        )
        self.assertEqual(result["layer"]["title"], "Street trees")
        self.assertEqual(Layer.objects.get(pk=self.draft.pk).title, "Street trees")


class StyleToolTests(WriteToolTestCase):
    def test_describe_style_shows_content(self):
        result = publishing.describe_style_for(OWN, provider_id=self.provider_id, style="green")
        self.assertEqual((result["format"], result["scope"]), ("sld", "workspace"))
        self.assertIn("<Name>green</Name>", result["content"])
        self.assertEqual(result["used_by_layers"], [("own_ws", "parks", "default")])

    def test_global_and_foreign_styles_are_not_writable(self):
        for style in ("shared", "theirs"):
            with self.subTest(style=style), self.assertRaises(ToolError):
                publishing.update_style_for(OWN, provider_id=self.provider_id, style=style, title="x")

    def test_invalid_content_is_not_saved(self):
        with self.assertRaises(ToolError) as raised:
            publishing.update_style_for(OWN, provider_id=self.provider_id, style="green", content="<Nope/>")
        self.assertIn("StyledLayerDescriptor", str(raised.exception))
        self.assertEqual(Style.objects.get(pk=self.green.pk).file_content, SLD.format("green"))

    @patch("tosca_api.apps.geodata_providers.services.commands.style_service.EngineClientFactory.create_client")
    def test_new_content_is_saved_and_uploaded(self, create_client):
        client = MagicMock()
        client.upload_style.return_value = {"success": True}
        create_client.return_value = client
        content = SLD.format("greener")
        result = publishing.update_style_for(OWN, provider_id=self.provider_id, style="own_ws:green", content=content)
        self.assertEqual(result, {"saved": True, "uploaded": True, "style": "own_ws:green"})
        client.upload_style.assert_called_once_with(
            name="green", content=content, style_format="sld", workspace="own_ws", overwrite=True,
        )
        saved = Style.objects.get(pk=self.green.pk)
        self.assertEqual((saved.file_content, saved.remote_state), (content, "SYNCED"))

    def test_switch_default_style_keeps_the_old_one_as_alternate(self):
        result = publishing.set_layer_default_style_for(
            OWN, provider_id=self.provider_id, workspace="own_ws", name="parks", style="red",
        )
        self.assertTrue(result["changed"])
        roles = dict(LayerStyleAssignment.objects.filter(layer=self.parks).values_list("style__name", "role"))
        self.assertEqual(roles, {"red": "default", "green": "alternate"})
        # And back: the alternate becomes the default again, no duplicates.
        publishing.set_layer_default_style_for(OWN, provider_id=self.provider_id, workspace="own_ws", name="parks", style="green")
        roles = dict(LayerStyleAssignment.objects.filter(layer=self.parks).values_list("style__name", "role"))
        self.assertEqual(roles, {"green": "default", "red": "alternate"})


class LayerGroupToolTests(WriteToolTestCase):
    def test_publish_runs_the_member_check(self):
        group = LayerGroup.objects.create(workspace=self.workspace, name="green_city", created_by=self.user)
        result = publishing.set_group_publication_for(
            OWN, provider_id=self.provider_id, workspace="own_ws", name="green_city", published=True,
        )
        # No members: the service refuses and records the reason.
        self.assertFalse(result["ok"])
        self.assertIn("at least two layers", result["error"])
        self.assertEqual(LayerGroup.objects.get(pk=group.pk).publishing_state, "FAILED")
        with self.assertRaises(ToolError):
            publishing.set_group_publication_for(
                OTHER, provider_id=self.provider_id, workspace="own_ws", name="green_city", published=True,
            )


class GeostoryWriteTests(WriteToolTestCase):
    def story(self, **kwargs):
        return story_tools.create_geostory_for(OWN, campaign_id=str(self.campaign.id), title="Green city", **kwargs)

    def test_create_in_own_campaign_only(self):
        created = self.story(text="First paragraph.\n\nSecond <b>one</b>.")
        self.assertEqual(created["status"], "draft")
        self.assertEqual([item["text"] for item in created["text"]], ["First paragraph.", "Second <b>one</b>."])
        self.assertEqual(GeoStory.objects.get(pk=created["id"]).author, self.user)
        with self.assertRaises(ToolError) as raised:
            story_tools.create_geostory_for(OWN, campaign_id=str(self.other_campaign.id), title="x")
        self.assertIn("does not belong to your organization", str(raised.exception))
        self.assertEqual([c["title"] for c in story_tools.list_campaigns_for(OWN)["campaigns"]], ["Own campaign"])

    def test_update_and_append_text(self):
        story_id = self.story()["id"]
        updated = story_tools.update_geostory_for(OWN, story_id=story_id, title="Greener city", append_text="More.")
        self.assertEqual((updated["title"], updated["text"]), ("Greener city", [{"text": "More."}]))
        with self.assertRaises(ToolError):
            story_tools.update_geostory_for(OTHER, story_id=story_id, title="taken over")

    def test_scenes_get_anchors_after_the_first(self):
        story_id = self.story(text="Intro.")["id"]
        camera = story_tools.Camera(center=[9.99, 53.55], zoom=12)
        layer = story_tools.SceneLayer(provider_id=self.provider_id, workspace="own_ws", name="parks", opacity=0.6)
        first = story_tools.add_scene_for(OWN, story_id=story_id, title="Overview", camera=camera, layers=[layer])
        second = story_tools.add_scene_for(OWN, story_id=story_id, title="Detail", bounds=[9.9, 53.5, 10.0, 53.6],
                                           text_before="Now closer.")
        self.assertFalse(first["anchored"])
        self.assertTrue(second["anchored"])
        self.assertEqual(first["scene"]["layers"][0]["style"], "green")
        story = story_tools.describe_geostory_for(OWN, story_id=story_id)
        self.assertEqual(story["text"], [{"text": "Intro."}, {"text": "Now closer."}, {"scene": second["scene"]["id"]}])
        self.assertEqual(story["unreachable_scenes"], [])

        story_tools.update_scene_for(OWN, story_id=story_id, scene_id=first["scene"]["id"], layers=[])
        self.assertEqual(story_tools.describe_geostory_for(OWN, story_id=story_id)["scenes"][0]["layers"], [])
        story_tools.delete_scene_for(OWN, story_id=story_id, scene_id=second["scene"]["id"])
        story = story_tools.describe_geostory_for(OWN, story_id=story_id)
        self.assertEqual((len(story["scenes"]), story["text"][-1]), (1, {"text": "Now closer."}))

    def test_scene_layers_must_be_catalog_layers(self):
        story_id = self.story()["id"]
        missing = story_tools.SceneLayer(provider_id=self.provider_id, workspace="own_ws", name="draft_trees")
        with self.assertRaises(ToolError):
            story_tools.add_scene_for(OWN, story_id=story_id, title="Broken", layers=[missing])
        # Nothing half-written.
        self.assertEqual(story_tools.describe_geostory_for(OWN, story_id=story_id)["scenes"], [])

    def test_scene_from_map_state(self):
        state = {
            "view": {"center": [9.99, 53.55], "zoom": 13.2, "bearing": 10, "pitch": 30, "bounds": [9.9, 53.5, 10.1, 53.6]},
            "layers": [
                {"id": "parks", "title": "Parks", "kind": "fill", "visible": True, "opacity": 0.7,
                 "catalog": {"providerId": self.provider_id, "workspace": "own_ws", "name": "parks", "style": "green"}},
                {"id": "drawn", "title": "My drawing", "kind": "fill", "visible": True, "opacity": 1},
                {"id": "hidden", "title": "Hidden", "kind": "fill", "visible": False, "opacity": 1,
                 "catalog": {"providerId": self.provider_id, "workspace": "own_ws", "name": "parks"}},
            ],
        }
        camera, bounds, layers, skipped = story_tools.scene_from_map_state(OWN, state)
        self.assertEqual((camera.zoom, camera.pitch, bounds), (13.2, 30, [9.9, 53.5, 10.1, 53.6]))
        self.assertEqual([(item.name, item.style, item.opacity) for item in layers], [("parks", "green", 0.7)])
        self.assertEqual(skipped, ["My drawing", "Hidden"])
