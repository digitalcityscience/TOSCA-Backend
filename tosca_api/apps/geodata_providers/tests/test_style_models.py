from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerStyleAssignment,
    Store,
    Style,
    Workspace,
)


class StyleModelTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='style-model-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Style Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )
        self.other_engine = GeodataEngine.objects.create(
            name='Other Style Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://other.example.com/geoserver',
            public_url='http://other.example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='style_ws',
            description='workspace',
            created_by=self.user,
        )
        self.other_workspace = Workspace.objects.create(
            geodata_engine=self.other_engine,
            name='other_style_ws',
            description='workspace',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            geodata_engine=self.engine,
            workspace=self.workspace,
            name='style_store',
            description='store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            created_by=self.user,
        )
        self.layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='roads',
            title='Roads',
            description='roads',
            table_name='roads',
            geometry_column='geom',
            geometry_type='LineString',
            srid=4326,
            created_by=self.user,
        )

    def _style(self, **overrides):
        defaults = {
            'geodata_engine': self.engine,
            'workspace': self.workspace,
            'name': 'roads_style',
            'title': 'Roads Style',
            'format': 'sld',
            'file_name': 'roads_style.sld',
            'file_content': '<StyledLayerDescriptor><NamedLayer /></StyledLayerDescriptor>',
            'validation_state': 'VALID',
            'created_by': self.user,
        }
        defaults.update(overrides)
        return Style.objects.create(**defaults)

    def test_style_sets_hash_and_helper_properties(self):
        style = self._style(remote_state='SYNCED')

        self.assertEqual(len(style.content_hash), 64)
        self.assertFalse(style.is_global)
        self.assertTrue(style.is_valid)
        self.assertTrue(style.is_synced)
        self.assertTrue(style.is_remote_supported)
        self.assertEqual(style.qualified_name, 'style_ws:roads_style')

    def test_global_style_qualified_name_uses_plain_name(self):
        style = self._style(workspace=None)

        self.assertTrue(style.is_global)
        self.assertEqual(style.qualified_name, 'roads_style')

    def test_style_rejects_workspace_from_different_engine(self):
        with self.assertRaises(ValidationError):
            self._style(workspace=self.other_workspace)

    def test_store_inherits_workspace_engine_when_missing(self):
        store = Store.objects.create(
            workspace=self.workspace,
            name='workspace_bound_store',
            description='store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            created_by=self.user,
        )

        self.assertEqual(store.geodata_engine, self.engine)

    def test_store_rejects_mismatched_workspace_engine(self):
        with self.assertRaises(ValidationError):
            Store.objects.create(
                geodata_engine=self.other_engine,
                workspace=self.workspace,
                name='mismatched_store',
                description='store',
                store_type='postgis',
                host='db',
                port=5432,
                database='gis',
                username='postgres',
                password='secret',
                schema='public',
                created_by=self.user,
            )

    def test_layer_rejects_store_from_different_workspace(self):
        other_workspace_same_engine = Workspace.objects.create(
            geodata_engine=self.engine,
            name='layer_other_ws',
            description='workspace',
            created_by=self.user,
        )
        other_store = Store.objects.create(
            workspace=other_workspace_same_engine,
            name='other_store',
            description='store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            created_by=self.user,
        )

        with self.assertRaises(ValidationError):
            Layer.objects.create(
                workspace=self.workspace,
                store=other_store,
                name='mismatched_layer',
                title='Mismatched Layer',
                description='desc',
                table_name='mismatched_layer',
                geometry_column='geom',
                geometry_type='Point',
                srid=4326,
                created_by=self.user,
            )

    def test_layer_accepts_raster_store(self):
        """
        Raster (geotiff) stores are first-class layer backings — the catalog
        builder branches on store_type to render raster vs vector layer
        detail shapes. Layer.clean() must not reject them.
        """
        raster_store = Store.objects.create(
            workspace=self.workspace,
            name='raster_store',
            description='store',
            store_type='geotiff',
            file_path='/tmp/data/heatmap.tif',
            charset='UTF-8',
            created_by=self.user,
        )

        layer = Layer.objects.create(
            workspace=self.workspace,
            store=raster_store,
            name='raster_layer',
            title='Raster Layer',
            description='desc',
            table_name='heatmap',
            geometry_column='rast',
            geometry_type='Polygon',
            srid=3857,
            created_by=self.user,
        )

        self.assertEqual(layer.store.store_type, 'geotiff')

    def test_layer_style_allows_style_from_different_engine(self):
        style = self._style(
            geodata_engine=self.other_engine,
            workspace=self.other_workspace,
            name='other_style',
        )

        assignment = LayerStyleAssignment.objects.create(
            layer=self.layer,
            style=style,
            role='default',
            is_active=True,
            created_by=self.user,
        )

        self.assertEqual(assignment.style, style)

    def test_layer_style_allows_workspace_scoped_style_from_different_workspace(self):
        other_workspace_same_engine = Workspace.objects.create(
            geodata_engine=self.engine,
            name='other_ws_same_engine',
            description='workspace',
            created_by=self.user,
        )
        style = self._style(
            workspace=other_workspace_same_engine,
            name='other_workspace_style',
        )

        assignment = LayerStyleAssignment.objects.create(
            layer=self.layer,
            style=style,
            role='default',
            is_active=True,
            created_by=self.user,
        )

        self.assertEqual(assignment.style, style)

    def test_layer_style_rejects_invalid_style(self):
        style = self._style(validation_state='INVALID', name='invalid_style')

        with self.assertRaises(ValidationError):
            LayerStyleAssignment.objects.create(
                layer=self.layer,
                style=style,
                role='default',
                is_active=True,
                created_by=self.user,
            )

    def test_layer_allows_only_one_active_default_style(self):
        first_style = self._style(name='first_style')
        second_style = self._style(name='second_style')
        LayerStyleAssignment.objects.create(
            layer=self.layer,
            style=first_style,
            role='default',
            is_active=True,
            created_by=self.user,
        )

        with self.assertRaises(ValidationError):
            LayerStyleAssignment.objects.create(
                layer=self.layer,
                style=second_style,
                role='default',
                is_active=True,
                created_by=self.user,
            )
