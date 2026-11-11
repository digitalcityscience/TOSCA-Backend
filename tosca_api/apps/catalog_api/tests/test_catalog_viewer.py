"""The catalog answers per caller: public items for everyone, plus the
caller's own organization's private workspaces (same rule as GeoServer's
workspace ACL), everything for platform admins."""

from django.urls import reverse
from rest_framework.test import APIClient

from tosca_api.apps.geodata_providers.models import Layer, Store, Style, Workspace
from tosca_api.apps.organizations.models import Organization

from .test_v1_api import CatalogV1ApiTestCase

GQ2_MEMBER = {"realm_access": {"roles": ["ROLE_GQ2_READER"]}, "organization": ["gq2"]}
DCS_MEMBER = {"realm_access": {"roles": ["ROLE_DCS_READER"]}, "organization": ["dcs"]}
PLATFORM_ADMIN = {"realm_access": {"roles": ["DJANGO_SUPERADMIN"]}}


class CatalogViewerTests(CatalogV1ApiTestCase):
    def setUp(self):
        super().setUp()
        gq2, _ = Organization.objects.get_or_create(slug="gq2", defaults={"name": "GQ2"})
        Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
        self.private_workspace = Workspace.objects.create(
            visibility="private",
            organization=gq2,
            geodata_engine=self.provider,
            name="gq2_internal",
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=self.private_workspace, geodata_engine=self.provider, name="gq2_store",
            store_type="postgis", host="db", port=5432, database="gis", username="postgres",
            password="secret", schema="public", created_by=self.user,
        )
        # Marked public, but its workspace is private: owner organization only.
        self.private_layer = Layer.objects.create(
            workspace=self.private_workspace, store=store, name="noise_survey", title="Noise survey",
            table_name="noise_survey", geometry_column="geom", geometry_type="Point", srid=4326,
            publishing_state="PUBLISHED", is_public=True, created_by=self.user,
        )
        self.private_style = Style.objects.create(
            geodata_engine=self.provider, workspace=self.private_workspace, name="gq2-noise",
            title="Noise", format="mbstyle", file_content='{"version": 8, "layers": []}',
            validation_state="VALID", created_by=self.user,
        )

    def _client(self, claims=None):
        client = APIClient()
        if claims is not None:
            client.force_authenticate(user=self.user, token=claims)
        return client

    def _workspace_names(self, client):
        response = client.get(reverse("catalog-v1-provider-workspace-list", kwargs={"provider_id": self.provider.id}))
        self.assertEqual(response.status_code, 200)
        return {item["name"] for item in response.json()["workspaces"]["workspace"]}

    def _layer_info(self, client):
        return client.get(reverse("catalog-v1-provider-layer-info", kwargs={
            "provider_id": self.provider.id, "workspace_name": "gq2_internal", "layer_name": "noise_survey",
        }))

    def test_private_workspace_is_hidden_from_anonymous_and_other_organizations(self):
        for client in (self._client(), self._client(DCS_MEMBER)):
            self.assertNotIn("gq2_internal", self._workspace_names(client))
            self.assertEqual(self._layer_info(client).status_code, 404)

    def test_owner_organization_and_platform_admin_see_it(self):
        for claims in (GQ2_MEMBER, PLATFORM_ADMIN):
            client = self._client(claims)
            self.assertIn("gq2_internal", self._workspace_names(client))
            self.assertEqual(self._layer_info(client).status_code, 200)

    def test_workspace_scoped_style_follows_its_workspace(self):
        url = reverse("catalog-v1-provider-style-detail", kwargs={"provider_id": self.provider.id, "style_ref": str(self.private_style.id)})
        self.assertEqual(self._client().get(url).status_code, 404)
        self.assertEqual(self._client(GQ2_MEMBER).get(url).status_code, 200)
        listed = self._client().get(reverse("catalog-v1-provider-style-list", kwargs={"provider_id": self.provider.id})).json()
        self.assertNotIn("gq2-noise", {style["name"] for style in listed})

    def test_answers_vary_by_caller_and_private_answers_are_not_cached(self):
        anonymous = self._client().get(reverse("catalog-v1-provider-workspace-list", kwargs={"provider_id": self.provider.id}))
        member = self._client(GQ2_MEMBER).get(reverse("catalog-v1-provider-workspace-list", kwargs={"provider_id": self.provider.id}))
        self.assertIn("Authorization", anonymous["Vary"])
        self.assertIn("Authorization", member["Vary"])
        self.assertEqual(member["Cache-Control"], "private, no-store")
