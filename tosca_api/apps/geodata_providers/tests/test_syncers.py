"""
Each syncer (WorkspaceSyncer/StoreSyncer/StyleSyncer/LayerSyncer) should be
unit-testable in isolation with a mocked GeoServer client — this is the
concrete thing the sync_service.py split was for. These tests construct
each syncer directly (not via GeoServerSyncService) to prove that.

test_geodata_engine_service.py and catalog_api's test_v1_api.py cover the
end-to-end / integration-level behavior through GeoServerSyncService itself.
"""
from unittest.mock import MagicMock

from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerStyleAssignment,
    Store,
    Style,
    Workspace,
)
from tosca_api.apps.geodata_providers.sync import (
    LayerSyncer,
    StoreSyncer,
    StyleSyncer,
    WorkspaceSyncer,
)


class SyncerTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='syncer-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Syncer Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )
        self.mock_client = MagicMock()


class WorkspaceSyncerTests(SyncerTestBase):
    def test_isolated_construction_needs_only_engine_and_client(self):
        syncer = WorkspaceSyncer(self.engine, self.mock_client)
        self.assertIs(syncer.engine, self.engine)
        self.assertIs(syncer.client, self.mock_client)

    def test_sync_workspaces_creates_from_mocked_client(self):
        self.mock_client.get_workspaces.return_value = ['mobility']
        syncer = WorkspaceSyncer(self.engine, self.mock_client)

        result = syncer.sync_workspaces(created_by=self.user)

        self.assertEqual(result['created'], 1)
        self.assertTrue(Workspace.objects.filter(geodata_engine=self.engine, name='mobility').exists())

    def test_inactive_engine_short_circuits_without_touching_client(self):
        self.engine.is_active = False
        self.engine.save(update_fields=['is_active'])
        syncer = WorkspaceSyncer(self.engine, self.mock_client)

        result = syncer.sync_workspaces(created_by=self.user)

        self.assertTrue(result['skipped'])
        self.mock_client.get_workspaces.assert_not_called()


class StoreSyncerTests(SyncerTestBase):
    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine, name='mobility', created_by=self.user,
        )

    def test_sync_stores_for_workspace_creates_from_mocked_client(self):
        self.mock_client.get_datastores.return_value = [
            {'name': 'gis', 'store_type': 'postgis', 'host': 'db', 'database': 'gis', 'username': 'pg'}
        ]
        syncer = StoreSyncer(self.engine, self.mock_client)

        result = syncer.sync_stores_for_workspace(self.workspace, created_by=self.user)

        self.assertEqual(result['created'], 1)
        self.mock_client.get_datastores.assert_called_once_with('mobility')
        self.assertTrue(Store.objects.filter(workspace=self.workspace, name='gis').exists())

    def test_incomplete_postgis_connection_details_are_skipped(self):
        self.mock_client.get_datastores.return_value = [
            {'name': 'incomplete', 'store_type': 'postgis'}
        ]
        syncer = StoreSyncer(self.engine, self.mock_client)

        result = syncer.sync_stores_for_workspace(self.workspace, created_by=self.user)

        self.assertEqual(result['created'], 0)
        self.assertFalse(Store.objects.filter(workspace=self.workspace, name='incomplete').exists())


class StyleSyncerTests(SyncerTestBase):
    def test_sync_styles_for_scope_creates_global_style_from_mocked_client(self):
        self.mock_client.get_styles.return_value = [{'name': 'roads'}]
        self.mock_client.get_style_content.return_value = {
            'content': '<StyledLayerDescriptor />',
            'format': 'sld',
            'file_name': 'roads.sld',
        }
        syncer = StyleSyncer(self.engine, self.mock_client)

        result = syncer.sync_styles_for_scope(None, created_by=self.user)

        self.assertEqual(result['created'], 1)
        self.assertTrue(
            Style.objects.filter(geodata_engine=self.engine, workspace__isnull=True, name='roads').exists()
        )


class LayerSyncerTests(SyncerTestBase):
    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine, name='mobility', created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='gis',
            store_type='postgis',
            host='db',
            database='gis',
            username='pg',
            created_by=self.user,
        )
        self.mock_client.get_featuretype_detail.return_value = {
            'geometry_type': 'Point',
            'geometry_column': 'geom',
            'srid': 4326,
        }

    def test_sync_layers_for_workspace_skips_layer_with_unknown_store(self):
        self.mock_client.get_layers.return_value = [
            {'name': 'roads', 'store_name': 'not_yet_synced'}
        ]
        syncer = LayerSyncer(self.engine, self.mock_client)

        result = syncer.sync_layers_for_workspace(self.workspace, created_by=self.user)

        self.assertEqual(result['created'], 0)
        self.assertEqual(result['errors'], [])
        self.assertFalse(Layer.objects.filter(workspace=self.workspace, name='roads').exists())

    def test_sync_layers_for_workspace_creates_and_assigns_default_style(self):
        Style.objects.create(
            geodata_engine=self.engine,
            name='roads_default',
            title='Roads Default',
            format='sld',
            created_by=self.user,
        )
        self.mock_client.get_layers.return_value = [
            {
                'name': 'roads',
                'store_name': 'gis',
                'title': 'Roads',
                'default_style_name': 'roads_default',
            }
        ]
        syncer = LayerSyncer(self.engine, self.mock_client)

        result = syncer.sync_layers_for_workspace(self.workspace, created_by=self.user)

        self.assertEqual(result['created'], 1)
        layer = Layer.objects.get(workspace=self.workspace, name='roads')
        self.assertTrue(layer.style_assignments.filter(style__name='roads_default', role='default').exists())

    def test_sync_keeps_authored_description_and_records_provider_abstract(self):
        authored = {
            'blocks': [
                {'type': 'header', 'data': {'text': 'Curated overview', 'level': 2}},
                {'type': 'paragraph', 'data': {'text': 'Written in TOSCA'}},
            ]
        }
        layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='districts',
            title='Districts',
            description_content=authored,
            table_name='districts',
            geometry_type='Polygon',
            created_by=self.user,
        )
        self.mock_client.get_layers.return_value = [
            {
                'name': 'districts',
                'store_name': 'gis',
                'title': 'Provider title',
                'abstract': 'Provider-maintained abstract',
            }
        ]

        result = LayerSyncer(self.engine, self.mock_client).sync_layers_for_workspace(
            self.workspace,
            created_by=self.user,
        )

        self.assertEqual(result['synced'], 1)
        layer.refresh_from_db()
        self.assertEqual(layer.description_content, authored)
        self.assertEqual(layer.description, 'Curated overview\n\nWritten in TOSCA')
        self.assertEqual(layer.provider_description, 'Provider-maintained abstract')

    def test_sync_preserves_curated_mbstyle_default_for_existing_vector_layer(self):
        layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='districts',
            title='Districts',
            table_name='districts',
            geometry_column='geom',
            geometry_type='Polygon',
            srid=4326,
            created_by=self.user,
        )
        local_style = Style.objects.create(
            geodata_engine=self.engine,
            workspace=self.workspace,
            name='district-colors',
            format='mbstyle',
            file_content=(
                '{"version":8,"sources":{},"layers":['
                '{"id":"district-fill","type":"fill",'
                '"source":"districts","source-layer":"districts"}]}'
            ),
            validation_state='VALID',
            created_by=self.user,
        )
        assignment = LayerStyleAssignment.objects.create(
            layer=layer,
            style=local_style,
            role=LayerStyleAssignment.Role.DEFAULT,
            is_active=True,
            created_by=self.user,
        )
        alternate_style = Style.objects.create(
            geodata_engine=self.engine,
            workspace=self.workspace,
            name='district-outline',
            format='mbstyle',
            file_content=(
                '{"version":8,"sources":{},"layers":['
                '{"id":"district-outline","type":"line",'
                '"source":"districts","source-layer":"districts"}]}'
            ),
            validation_state='VALID',
            created_by=self.user,
        )
        alternate_assignment = LayerStyleAssignment.objects.create(
            layer=layer,
            style=alternate_style,
            role=LayerStyleAssignment.Role.ALTERNATE,
            is_active=True,
            created_by=self.user,
        )
        Style.objects.create(
            geodata_engine=self.engine,
            name='polygon',
            format='sld',
            created_by=self.user,
        )
        self.mock_client.get_layers.return_value = [
            {
                'name': 'districts',
                'store_name': 'gis',
                'title': 'Districts',
                'default_style_name': 'polygon',
            }
        ]

        result = LayerSyncer(self.engine, self.mock_client).sync_layers_for_workspace(
            self.workspace,
            created_by=self.user,
        )

        self.assertEqual(result['synced'], 1)
        assignment.refresh_from_db()
        alternate_assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)
        self.assertEqual(assignment.role, LayerStyleAssignment.Role.DEFAULT)
        self.assertEqual(assignment.style_layer_ids, ['district-fill'])
        self.assertTrue(alternate_assignment.is_active)
        self.assertEqual(alternate_assignment.style_layer_ids, ['district-outline'])
        self.assertFalse(
            layer.style_assignments.filter(style__name='polygon').exists()
        )

    def test_missing_summary_geometry_is_resolved_from_featuretype_detail(self):
        self.mock_client.get_layers.return_value = [
            {'name': 'districts', 'store_name': 'gis', 'title': 'Districts'}
        ]
        self.mock_client.get_featuretype_detail.return_value = {
            'geometry_type': 'org.locationtech.jts.geom.MultiPolygon',
            'geometry_column': 'shape',
            'srid': 25832,
        }

        result = LayerSyncer(self.engine, self.mock_client).sync_layers_for_workspace(
            self.workspace,
            created_by=self.user,
        )

        self.assertEqual(result['created'], 1)
        layer = Layer.objects.get(workspace=self.workspace, name='districts')
        self.assertEqual(layer.geometry_type, 'MultiPolygon')
        self.assertEqual(layer.geometry_column, 'shape')
        self.assertEqual(layer.srid, 25832)

    def test_unknown_vector_geometry_is_rejected_instead_of_defaulting_to_point(self):
        self.mock_client.get_layers.return_value = [
            {'name': 'unknown', 'store_name': 'gis'}
        ]
        self.mock_client.get_featuretype_detail.return_value = {}

        result = LayerSyncer(self.engine, self.mock_client).sync_layers_for_workspace(
            self.workspace,
            created_by=self.user,
        )

        self.assertEqual(result['created'], 0)
        self.assertIn('did not report a supported geometry type', result['errors'][0])
        self.assertFalse(Layer.objects.filter(workspace=self.workspace, name='unknown').exists())
