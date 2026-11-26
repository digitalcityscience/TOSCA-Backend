"""Catalog tools show a caller what the catalog API shows them, nothing more."""

from mcp.server.mcpserver.exceptions import ToolError

from tosca_api.apps.catalog_api.tests import test_v1_api
from tosca_api.apps.geodata_providers.models import Layer, Store, Workspace
from tosca_api.apps.mcp_server.principal import McpPrincipal
from tosca_api.apps.mcp_server.tools.catalog import (
    describe_layer_for,
    list_workspaces_for,
    search_layers_for,
)
from tosca_api.apps.organizations.models import Organization

GQ2 = McpPrincipal(sub="s1", username="gq2-reader", org_slug="gq2", level="READER", exempt=False)
DCS = McpPrincipal(sub="s2", username="dcs-reader", org_slug="dcs", level="READER", exempt=False)
ADMIN = McpPrincipal(sub="s3", username="root", org_slug=None, level=None, exempt=True)


class CatalogToolTests(test_v1_api.CatalogV1ApiTestCase):
    def setUp(self):
        super().setUp()
        gq2, _ = Organization.objects.get_or_create(slug="gq2", defaults={"name": "GQ2"})
        Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
        private_workspace = Workspace.objects.create(
            visibility="private", organization=gq2, geodata_engine=self.provider,
            name="gq2_internal", created_by=self.user,
        )
        store = Store.objects.create(
            workspace=private_workspace, geodata_engine=self.provider, name="gq2_store",
            store_type="postgis", host="db", port=5432, database="gis", username="postgres",
            password="secret", schema="public", created_by=self.user,
        )
        Layer.objects.create(
            workspace=private_workspace, store=store, name="noise_survey", title="Noise survey",
            description="Measured   noise\nlevels", table_name="noise_survey", geometry_column="geom",
            geometry_type="Point", srid=4326, publishing_state="PUBLISHED", is_public=True,
            attributes=[{"name": "db", "type": "number"}], bounds=[9.8, 53.4, 10.2, 53.7],
            created_by=self.user,
        )

    def names(self, principal, **kwargs):
        return {layer["name"] for layer in search_layers_for(principal, **kwargs)["layers"]}

    def test_private_layers_are_listed_for_the_owner_organization_only(self):
        self.assertIn("noise_survey", self.names(GQ2))
        self.assertIn("noise_survey", self.names(ADMIN))
        self.assertNotIn("noise_survey", self.names(DCS))
        # Public layers are there for everyone; drafts for nobody.
        for principal in (GQ2, DCS, ADMIN):
            self.assertIn("tram_lines", self.names(principal))
            self.assertNotIn("draft_layer", self.names(principal))

    def test_same_layers_as_the_catalog_api(self):
        from tosca_api.apps.catalog_api.services.v1.visibility_service import CatalogVisibilityService

        for principal in (GQ2, DCS, ADMIN):
            expected = {
                layer.name for layer in CatalogVisibilityService.list_visible_layers(
                    provider_id=self.provider.id, viewer=principal.viewer
                )
            }
            self.assertEqual(self.names(principal, limit=100), expected)

    def test_search_matches_all_words_and_pages(self):
        self.assertEqual(self.names(GQ2, query="noise survey"), {"noise_survey"})
        self.assertEqual(self.names(GQ2, query="noise tram"), set())
        first = search_layers_for(ADMIN, limit=1)
        self.assertEqual(len(first["layers"]), 1)
        self.assertEqual(first["next_offset"], 1)
        self.assertGreater(first["total"], 1)
        last = search_layers_for(ADMIN, limit=100, offset=first["total"] - 1)
        self.assertIsNone(last["next_offset"])

    def test_workspace_filter_does_not_reveal_a_private_workspace(self):
        self.assertEqual(self.names(GQ2, workspace="gq2_internal"), {"noise_survey"})
        self.assertEqual(search_layers_for(DCS, workspace="gq2_internal")["total"], 0)

    def test_workspaces(self):
        own = {item["name"] for item in list_workspaces_for(GQ2)["workspaces"]}
        other = {item["name"] for item in list_workspaces_for(DCS)["workspaces"]}
        self.assertIn("gq2_internal", own)
        self.assertNotIn("gq2_internal", other)
        self.assertIn("mobility", other)

    def test_describe_layer(self):
        detail = describe_layer_for(
            GQ2, provider_id=str(self.provider.id), workspace="gq2_internal", name="noise_survey"
        )
        self.assertEqual(detail["title"], "Noise survey")
        self.assertEqual(detail["description"], "Measured noise levels")
        self.assertEqual(detail["attributes"], [{"name": "db", "type": "number"}])
        self.assertEqual(detail["extent_wgs84"], [9.8, 53.4, 10.2, 53.7])
        self.assertEqual((detail["kind"], detail["public"]), ("vector", False))

    def test_describe_gives_one_answer_for_missing_and_foreign_layers(self):
        messages = []
        for workspace, name in (("gq2_internal", "noise_survey"), ("mobility", "does_not_exist")):
            with self.assertRaises(ToolError) as raised:
                describe_layer_for(DCS, provider_id=str(self.provider.id), workspace=workspace, name=name)
            messages.append(str(raised.exception).replace(f"{workspace}:{name}", "X"))
        self.assertEqual(messages[0], messages[1])

    def test_no_credentials_or_internal_addresses_in_answers(self):
        text = str([
            list_workspaces_for(ADMIN),
            search_layers_for(ADMIN, limit=100),
            describe_layer_for(ADMIN, provider_id=str(self.provider.id), workspace="mobility", name="tram_lines"),
        ])
        for secret in ("secret", "catalog.internal", "admin_password", "postgres"):
            self.assertNotIn(secret, text)
