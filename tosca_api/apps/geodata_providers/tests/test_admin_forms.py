import json
from io import BytesIO
from unittest.mock import MagicMock, patch

from PIL import Image
from django.contrib.auth.models import User
from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import QueryDict
from django.test import RequestFactory
from django.test import TestCase
from django.urls import reverse

from tosca_api.apps.geodata_providers.admin import (
    DeleteAborted,
    LayerAdmin,
    LayerStyleInline,
    StoreAdmin,
    StoreAdminForm,
    SpriteAssetAdmin,
    SpriteAssetAdminForm,
    StyleAdmin,
    WorkspaceAdmin,
    WorkspaceAdminForm,
)
from tosca_api.apps.geodata_providers.admin_actions.layer import publish_layer, unpublish_layer
from tosca_api.apps.geodata_providers.admin_forms import PublishPostGISForm
from tosca_api.apps.geodata_providers.admin_views.layer import publish_postgis_view
from tosca_api.apps.geodata_providers.admin_views.store import store_clone_view
from tosca_api.apps.geodata_providers.geoserver.client import GeoServerClient
from tosca_api.apps.geodata_providers.admin_views.layer import tables_for_store_view
from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerStyleAssignment,
    SpriteAsset,
    Store,
    Style,
    Workspace,
)


class SpriteAssetAdminFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='sprite-admin')
        self.engine = GeodataEngine.objects.create(
            name='Sprite Engine',
            description='sprite test engine',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )

    @staticmethod
    def _png_upload(*, size=(2, 2), name='sprite.png'):
        image_bytes = BytesIO()
        Image.new('RGBA', size, (255, 255, 0, 255)).save(image_bytes, format='PNG')
        return SimpleUploadedFile(name, image_bytes.getvalue(), content_type='image/png')

    def test_index_file_populates_optional_index_content(self):
        index = {
            'test-pattern': {
                'x': 0,
                'y': 0,
                'width': 2,
                'height': 2,
                'pixelRatio': 1,
            },
        }
        form = SpriteAssetAdminForm(
            data={
                'geodata_engine': str(self.engine.pk),
                'workspace': '',
                'name': 'test-patterns',
            },
            files={
                'image': self._png_upload(),
                'index_file': SimpleUploadedFile(
                    'sprite.json',
                    json.dumps(index).encode('utf-8'),
                    content_type='application/json',
                ),
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['index_content'], index)

    def test_high_dpi_files_populate_and_validate_as_a_pair(self):
        index = {
            'test-pattern': {
                'x': 0,
                'y': 0,
                'width': 2,
                'height': 2,
                'pixelRatio': 1,
            },
        }
        index_2x = {
            'test-pattern': {
                'x': 0,
                'y': 0,
                'width': 4,
                'height': 4,
                'pixelRatio': 2,
            },
        }
        form = SpriteAssetAdminForm(
            data={
                'geodata_engine': str(self.engine.pk),
                'workspace': '',
                'name': 'retina-patterns',
            },
            files={
                'image': self._png_upload(),
                'index_file': SimpleUploadedFile(
                    'sprite.json',
                    json.dumps(index).encode('utf-8'),
                    content_type='application/json',
                ),
                'image_2x': self._png_upload(size=(4, 4), name='sprite@2x.png'),
                'index_file_2x': SimpleUploadedFile(
                    'sprite@2x.json',
                    json.dumps(index_2x).encode('utf-8'),
                    content_type='application/json',
                ),
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['index_content_2x'], index_2x)

    def test_high_dpi_png_requires_matching_index(self):
        index = {
            'test-pattern': {
                'x': 0,
                'y': 0,
                'width': 2,
                'height': 2,
                'pixelRatio': 1,
            },
        }
        form = SpriteAssetAdminForm(
            data={
                'geodata_engine': str(self.engine.pk),
                'workspace': '',
                'name': 'incomplete-retina-patterns',
                'index_content': json.dumps(index),
            },
            files={
                'image': self._png_upload(),
                'image_2x': self._png_upload(size=(4, 4), name='sprite@2x.png'),
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn('index_content_2x', form.errors)

    def test_admin_preview_registers_live_assets_and_saved_sprite_data(self):
        sprite = SpriteAsset(
            geodata_engine=self.engine,
            name='preview-patterns',
            image=self._png_upload(),
            index_content={
                'test-pattern': {
                    'x': 0,
                    'y': 0,
                    'width': 2,
                    'height': 2,
                    'pixelRatio': 1,
                },
            },
            created_by=self.user,
        )
        model_admin = SpriteAssetAdmin(SpriteAsset, AdminSite())

        preview = str(model_admin.sprite_preview(sprite))
        media = str(model_admin.media)

        self.assertIn('data-sprite-preview', preview)
        self.assertIn('test-pattern', preview)
        self.assertIn('admin/js/sprite_asset_preview.js', media)
        self.assertIn('admin/css/sprite_asset_preview.css', media)

    def test_index_content_is_required_without_index_file(self):
        form = SpriteAssetAdminForm(
            data={
                'geodata_engine': str(self.engine.pk),
                'workspace': '',
                'name': 'test-patterns',
            },
            files={'image': self._png_upload()},
        )

        self.assertFalse(form.is_valid())
        self.assertIn('index_content', form.errors)


class LayerStyleAssignmentInlineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='layer-style-admin',
            is_staff=True,
            is_superuser=True,
        )
        self.engine = GeodataEngine.objects.create(
            name='Layer Style Engine',
            description='inline test engine',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='style_inline_ws',
            description='inline test workspace',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='style_inline_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='inline test store',
            created_by=self.user,
        )
        self.layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='style_inline_layer',
            title='Style inline layer',
            table_name='style_inline_layer',
            geometry_column='geom',
            geometry_type='Polygon',
            srid=4326,
            created_by=self.user,
        )
        self.first_style = self._style('first-inline-style')
        self.second_style = self._style('second-inline-style')
        self.default_assignment = LayerStyleAssignment.objects.create(
            layer=self.layer,
            style=self.first_style,
            role=LayerStyleAssignment.Role.DEFAULT,
            is_active=True,
            created_by=self.user,
        )
        self.request = RequestFactory().post('/admin/geodata_providers/layer/change/')
        self.request.user = self.user
        self.site = AdminSite()

    def _style(self, name):
        return Style.objects.create(
            geodata_engine=self.engine,
            workspace=self.workspace,
            name=name,
            format='mbstyle',
            file_name=f'{name}.mbstyle',
            file_content='{"version":8,"sources":{},"layers":[]}',
            validation_state='VALID',
            created_by=self.user,
        )

    def _formset(self, rows, initial_forms):
        inline = LayerStyleInline(Layer, self.site)
        formset_class = inline.get_formset(self.request, self.layer)
        prefix = formset_class.get_default_prefix()
        data = {
            f'{prefix}-TOTAL_FORMS': str(len(rows)),
            f'{prefix}-INITIAL_FORMS': str(initial_forms),
            f'{prefix}-MIN_NUM_FORMS': '0',
            f'{prefix}-MAX_NUM_FORMS': '1000',
        }
        for index, row in enumerate(rows):
            for field, value in row.items():
                data[f'{prefix}-{index}-{field}'] = value
        return formset_class(data=data, instance=self.layer, prefix=prefix)

    @staticmethod
    def _row(assignment, *, style, role, is_active=True):
        row = {
            'id': '' if assignment is None else str(assignment.pk),
            'style': str(style.pk),
            'role': role,
            'style_layer_ids': '[]',
        }
        if is_active:
            row['is_active'] = 'on'
        return row

    def test_duplicate_defaults_are_reported_as_formset_error(self):
        formset = self._formset(
            [
                self._row(
                    self.default_assignment,
                    style=self.first_style,
                    role=LayerStyleAssignment.Role.DEFAULT,
                ),
                self._row(
                    None,
                    style=self.second_style,
                    role=LayerStyleAssignment.Role.DEFAULT,
                ),
            ],
            initial_forms=1,
        )

        self.assertFalse(formset.is_valid())
        self.assertIn(
            'Only one active default style is allowed per layer.',
            formset.non_form_errors(),
        )

    def test_new_uuid_assignment_receives_created_by(self):
        formset = self._formset(
            [
                self._row(
                    self.default_assignment,
                    style=self.first_style,
                    role=LayerStyleAssignment.Role.DEFAULT,
                ),
                self._row(
                    None,
                    style=self.second_style,
                    role=LayerStyleAssignment.Role.ALTERNATE,
                ),
            ],
            initial_forms=1,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        LayerAdmin(Layer, self.site).save_formset(
            self.request,
            form=None,
            formset=formset,
            change=True,
        )

        created = LayerStyleAssignment.objects.get(style=self.second_style)
        self.assertEqual(created.created_by, self.user)

    def test_blank_mbstyle_layer_selection_is_inferred_from_assigned_layer(self):
        inferred_style = Style.objects.create(
            geodata_engine=self.engine,
            workspace=self.workspace,
            name='inferred-inline-style',
            format='mbstyle',
            file_name='inferred-inline-style.mbstyle',
            file_content=json.dumps({
                'version': 8,
                'sources': {},
                'layers': [
                    {
                        'id': 'inferred-fill',
                        'type': 'fill',
                        'source': self.layer.name,
                        'source-layer': self.layer.name,
                    },
                    {
                        'id': 'inferred-outline',
                        'type': 'line',
                        'source': self.layer.name,
                        'source-layer': self.layer.name,
                    },
                ],
            }),
            validation_state='VALID',
            created_by=self.user,
        )
        formset = self._formset(
            [
                self._row(
                    self.default_assignment,
                    style=self.first_style,
                    role=LayerStyleAssignment.Role.DEFAULT,
                ),
                self._row(
                    None,
                    style=inferred_style,
                    role=LayerStyleAssignment.Role.ALTERNATE,
                ),
            ],
            initial_forms=1,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        LayerAdmin(Layer, self.site).save_formset(
            self.request,
            form=None,
            formset=formset,
            change=True,
        )

        assignment = LayerStyleAssignment.objects.get(style=inferred_style)
        self.assertEqual(
            assignment.style_layer_ids,
            ['inferred-fill', 'inferred-outline'],
        )

    def test_default_assignment_can_be_swapped_in_one_save(self):
        alternate = LayerStyleAssignment.objects.create(
            layer=self.layer,
            style=self.second_style,
            role=LayerStyleAssignment.Role.ALTERNATE,
            is_active=True,
            created_by=self.user,
        )
        formset = self._formset(
            [
                self._row(
                    self.default_assignment,
                    style=self.first_style,
                    role=LayerStyleAssignment.Role.ALTERNATE,
                ),
                self._row(
                    alternate,
                    style=self.second_style,
                    role=LayerStyleAssignment.Role.DEFAULT,
                ),
            ],
            initial_forms=2,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        LayerAdmin(Layer, self.site).save_formset(
            self.request,
            form=None,
            formset=formset,
            change=True,
        )

        self.default_assignment.refresh_from_db()
        alternate.refresh_from_db()
        self.assertEqual(self.default_assignment.role, LayerStyleAssignment.Role.ALTERNATE)
        self.assertEqual(alternate.role, LayerStyleAssignment.Role.DEFAULT)

    def test_admin_post_persists_bold_description_and_default_style_swap(self):
        alternate = LayerStyleAssignment.objects.create(
            layer=self.layer,
            style=self.second_style,
            role=LayerStyleAssignment.Role.ALTERNATE,
            is_active=True,
            created_by=self.user,
        )
        description_content = {
            'blocks': [
                {
                    'type': 'paragraph',
                    'data': {'text': '<strong>Bold metadata</strong> survives style changes.'},
                },
            ],
        }
        self.client.force_login(self.user)
        change_url = reverse(
            'admin:geodata_providers_layer_change',
            args=[self.layer.id],
        )
        payload = {
            'title': self.layer.title,
            'description_content': json.dumps(description_content),
            'srid': str(self.layer.srid),
            'queryable': 'on',
            'style_assignments-TOTAL_FORMS': '2',
            'style_assignments-INITIAL_FORMS': '2',
            'style_assignments-MIN_NUM_FORMS': '0',
            'style_assignments-MAX_NUM_FORMS': '1000',
            'style_assignments-0-id': str(self.default_assignment.id),
            'style_assignments-0-style': str(self.first_style.id),
            'style_assignments-0-role': LayerStyleAssignment.Role.ALTERNATE,
            'style_assignments-0-style_layer_ids': '[]',
            'style_assignments-0-is_active': 'on',
            'style_assignments-1-id': str(alternate.id),
            'style_assignments-1-style': str(self.second_style.id),
            'style_assignments-1-role': LayerStyleAssignment.Role.DEFAULT,
            'style_assignments-1-style_layer_ids': '[]',
            'style_assignments-1-is_active': 'on',
            '_save': 'Save',
        }

        response = self.client.post(change_url, payload)

        self.assertEqual(response.status_code, 302)
        self.layer.refresh_from_db()
        self.default_assignment.refresh_from_db()
        alternate.refresh_from_db()
        self.assertEqual(self.layer.description_content, description_content)
        self.assertEqual(
            self.layer.description,
            'Bold metadata survives style changes.',
        )
        self.assertEqual(
            self.default_assignment.role,
            LayerStyleAssignment.Role.ALTERNATE,
        )
        self.assertEqual(alternate.role, LayerStyleAssignment.Role.DEFAULT)

        reload_response = self.client.get(change_url)
        self.assertEqual(reload_response.status_code, 200)
        reloaded_layer = reload_response.context['adminform'].form.instance
        self.assertEqual(reloaded_layer.description_content, description_content)


class InactiveProviderAdminVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='inactiveadmin',
            email='inactiveadmin@example.com',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.active_engine = GeodataEngine.objects.create(
            name='Active Admin Engine',
            description='active',
            base_url='http://active.example.com/geoserver',
            public_url='http://active.example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            created_by=self.user,
        )
        self.inactive_engine = GeodataEngine.objects.create(
            name='Inactive Admin Engine',
            description='inactive',
            base_url='http://inactive.example.com/geoserver',
            public_url='http://inactive.example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=False,
            created_by=self.user,
        )
        self.active_workspace = Workspace.objects.create(
            geodata_engine=self.active_engine,
            name='active_ws',
            description='active',
            created_by=self.user,
        )
        self.inactive_workspace = Workspace.objects.create(
            geodata_engine=self.inactive_engine,
            name='inactive_ws',
            description='inactive',
            created_by=self.user,
        )
        self.active_store = Store.objects.create(
            workspace=self.active_workspace,
            geodata_engine=self.active_engine,
            name='active_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='active',
            created_by=self.user,
        )
        self.inactive_store = Store.objects.create(
            workspace=self.inactive_workspace,
            geodata_engine=self.inactive_engine,
            name='inactive_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='inactive',
            created_by=self.user,
        )
        self.active_layer = Layer.objects.create(
            workspace=self.active_workspace,
            store=self.active_store,
            name='active_layer',
            title='Active Layer',
            table_name='active_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        self.inactive_layer = Layer.objects.create(
            workspace=self.inactive_workspace,
            store=self.inactive_store,
            name='inactive_layer',
            title='Inactive Layer',
            table_name='inactive_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        self.active_style = Style.objects.create(
            geodata_engine=self.active_engine,
            name='active_style',
            title='Active Style',
            format='sld',
            file_content='<StyledLayerDescriptor />',
            created_by=self.user,
        )
        self.inactive_style = Style.objects.create(
            geodata_engine=self.inactive_engine,
            name='inactive_style',
            title='Inactive Style',
            format='sld',
            file_content='<StyledLayerDescriptor />',
            created_by=self.user,
        )
        self.site = AdminSite()
        self.request_factory = RequestFactory()
        self.request = self.request_factory.get('/admin/geodata_providers/')
        self.request.user = self.user

    def test_child_admin_changelists_hide_inactive_provider_resources(self):
        admin_expectations = [
            (WorkspaceAdmin(Workspace, self.site), self.active_workspace, self.inactive_workspace),
            (StoreAdmin(Store, self.site), self.active_store, self.inactive_store),
            (LayerAdmin(Layer, self.site), self.active_layer, self.inactive_layer),
            (StyleAdmin(Style, self.site), self.active_style, self.inactive_style),
        ]

        for model_admin, visible_obj, hidden_obj in admin_expectations:
            with self.subTest(model=model_admin.model.__name__):
                queryset = model_admin.get_queryset(self.request)

                self.assertIn(visible_obj, queryset)
                self.assertNotIn(hidden_obj, queryset)

    def test_publish_postgis_form_hides_inactive_provider_resources(self):
        form = PublishPostGISForm()

        self.assertIn(self.active_workspace, form.fields['workspace'].queryset)
        self.assertNotIn(self.inactive_workspace, form.fields['workspace'].queryset)
        self.assertIn(self.active_store, form.fields['store'].queryset)
        self.assertNotIn(self.inactive_store, form.fields['store'].queryset)


class AdminFormCreateFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='adminforms',
            email='adminforms@example.com',
            password='testpass123',
        )
        self.engine = GeodataEngine.objects.create(
            name='Admin Form Engine',
            description='test',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            created_by=self.user,
        )
        self.site = AdminSite()
        self.request_factory = RequestFactory()

    def test_workspace_add_form_rejects_reserved_name(self):
        form = WorkspaceAdminForm(
            data={
                'geodata_engine': str(self.engine.pk),
                'name': 'vector',
                'description': 'desc',
                'created_by': str(self.user.pk),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('reserved', form.errors['name'][0])

    @patch('tosca_api.apps.geodata_providers.admin.StoreService.test_store_connection')
    def test_store_add_form_validates_connection_without_remote_create(self, mock_test_store_connection):
        mock_test_store_connection.return_value = {
            'success': True,
            'message': 'PostGIS connection validated.',
            'details': {'schema_exists': True},
        }
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='store_ws',
            description='desc',
            created_by=self.user,
        )

        form = StoreAdminForm(
            data={
                'workspace': str(workspace.pk),
                'geodata_engine': '',
                'name': 'new_store',
                'store_type': 'postgis',
                'description': 'desc',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'username': 'postgres',
                'password': 'secret',
                'schema': 'public',
                'file_path': '',
                'charset': 'UTF-8',
                'created_by': str(self.user.pk),
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        mock_test_store_connection.assert_called_once_with(
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
        )

    @patch('tosca_api.apps.geodata_providers.admin.StoreService.test_store_connection')
    def test_store_add_form_rejects_failed_connection_validation(self, mock_test_store_connection):
        mock_test_store_connection.return_value = {
            'success': False,
            'error': 'Could not connect to PostGIS.',
            'message': 'Could not connect to PostGIS.',
            'details': {},
        }
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='invalid_store_ws',
            description='desc',
            created_by=self.user,
        )

        form = StoreAdminForm(
            data={
                'workspace': str(workspace.pk),
                'geodata_engine': '',
                'name': 'invalid_store',
                'store_type': 'postgis',
                'description': 'desc',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'username': 'postgres',
                'password': 'wrong',
                'schema': 'public',
                'file_path': '',
                'charset': 'UTF-8',
                'created_by': str(self.user.pk),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Could not connect to PostGIS.', form.non_field_errors())

    def test_store_add_form_requires_file_path_for_file_stores(self):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='file_store_ws',
            description='desc',
            created_by=self.user,
        )

        form = StoreAdminForm(
            data={
                'workspace': str(workspace.pk),
                'geodata_engine': '',
                'name': 'file_store',
                'store_type': 'file',
                'description': 'desc',
                'host': '',
                'port': 5432,
                'database': '',
                'username': '',
                'password': '',
                'schema': 'public',
                'file_path': '',
                'charset': 'UTF-8',
                'created_by': str(self.user.pk),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors['file_path'],
            ['This field is required for file stores.'],
        )

    @patch('tosca_api.apps.geodata_providers.admin._run_workspace_sync')
    @patch('tosca_api.apps.geodata_providers.admin.StoreService.create_postgis_store')
    def test_store_admin_save_model_uses_service(self, mock_create_postgis_store, mock_run_workspace_sync):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='save_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store(
            workspace=workspace,
            name='store_to_save',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        request = self.request_factory.post('/admin/geodata_providers/store/add/')
        request.user = self.user
        model_admin = StoreAdmin(Store, self.site)
        form = MagicMock()
        form.cleaned_data = {'password': 'secret'}
        saved_store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='store_to_save',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        mock_create_postgis_store.return_value = {'success': True, 'resource': saved_store}

        model_admin.save_model(request, store, form, change=False)

        self.assertEqual(store.geodata_engine, self.engine)
        mock_create_postgis_store.assert_called_once()
        mock_run_workspace_sync.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin.StoreService.test_store_connection')
    def test_store_edit_form_allows_connection_correction_without_layers(self, mock_test_store_connection):
        mock_test_store_connection.return_value = {
            'success': True,
            'message': 'PostGIS connection validated.',
            'details': {'schema_exists': True},
        }
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='editable_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='editable_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )

        form = StoreAdminForm(
            data={
                'workspace': str(workspace.pk),
                'geodata_engine': str(self.engine.pk),
                'name': 'editable_store',
                'store_type': 'postgis',
                'description': 'desc',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'username': 'postgres',
                'password': '',
                'schema': 'gis_schema',
                'file_path': '',
                'charset': 'UTF-8',
                'created_by': str(self.user.pk),
            },
            instance=store,
        )

        self.assertTrue(form.is_valid(), form.errors)
        mock_test_store_connection.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin.StoreService.test_store_connection')
    def test_store_edit_form_allows_connection_correction_with_layers(self, mock_test_store_connection):
        mock_test_store_connection.return_value = {
            'success': True,
            'message': 'PostGIS connection validated.',
            'details': {'schema_exists': True},
        }
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='locked_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='locked_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=workspace,
            store=store,
            name='locked_layer',
            title='Locked Layer',
            table_name='locked_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            created_by=self.user,
        )

        form = StoreAdminForm(
            data={
                'workspace': str(workspace.pk),
                'geodata_engine': str(self.engine.pk),
                'name': 'locked_store',
                'store_type': 'postgis',
                'description': 'desc',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'username': 'postgres',
                'password': '',
                'schema': 'gis_schema',
                'file_path': '',
                'charset': 'UTF-8',
                'created_by': str(self.user.pk),
            },
            instance=store,
        )

        self.assertTrue(form.is_valid(), form.errors)
        mock_test_store_connection.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin._run_workspace_sync')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.test_postgis_connection')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_store_admin_save_model_updates_existing_connection(
        self,
        mock_create_client,
        mock_test_postgis_connection,
        mock_run_workspace_sync,
    ):
        mock_test_postgis_connection.return_value = {'schema_exists': True}
        mock_create_client.return_value.update_postgis_store.return_value = {
            'success': True,
            'verified': True,
            'message': 'updated',
        }
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='save_update_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='save_update_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        request = self.request_factory.post(f'/admin/geodata_providers/store/{store.pk}/change/')
        request.user = self.user
        model_admin = StoreAdmin(Store, self.site)
        form = StoreAdminForm(
            data={
                'workspace': str(workspace.pk),
                'geodata_engine': str(self.engine.pk),
                'name': 'save_update_store',
                'store_type': 'postgis',
                'description': 'desc',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'username': 'postgres',
                'password': '',
                'schema': 'gis_schema',
                'file_path': '',
                'charset': 'UTF-8',
                'created_by': str(self.user.pk),
            },
            instance=store,
        )

        self.assertTrue(form.is_valid(), form.errors)
        changed_store = form.save(commit=False)
        model_admin.save_model(request, changed_store, form, change=True)

        store.refresh_from_db()
        self.assertEqual(store.schema, 'gis_schema')
        mock_create_client.return_value.update_postgis_store.assert_called_once()
        mock_run_workspace_sync.assert_called_once()

    def test_store_admin_delete_is_blocked_when_layers_exist(self):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='store_with_layer',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=workspace,
            store=store,
            name='dependent_layer',
            title='Dependent Layer',
            table_name='dependent_table',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            created_by=self.user,
        )
        request = self.request_factory.post('/admin/geodata_providers/store/')
        request.user = self.user
        model_admin = StoreAdmin(Store, self.site)

        with self.assertRaises(DeleteAborted):
            model_admin.delete_model(request, store)

    @patch('tosca_api.apps.geodata_providers.admin.EngineClientFactory.create_client')
    def test_style_admin_delete_model_deletes_remote_first(self, mock_create_client):
        client = MagicMock()
        client.delete_style.return_value = {'success': True}
        mock_create_client.return_value = client

        style = Style.objects.create(
            geodata_engine=self.engine,
            name='delete_style',
            title='Delete Style',
            format='mbstyle',
            file_name='delete_style.mbstyle',
            file_content='{"version":8,"layers":[]}',
            validation_state='VALID',
            created_by=self.user,
        )
        request = self.request_factory.post('/admin/geodata_providers/style/')
        request.user = self.user
        model_admin = StyleAdmin(Style, self.site)

        model_admin.delete_model(request, style)

        client.delete_style.assert_called_once_with(name='delete_style', workspace=None)
        self.assertFalse(Style.objects.filter(pk=style.pk).exists())

    @patch('tosca_api.apps.geodata_providers.admin.EngineClientFactory.create_client')
    def test_style_admin_delete_model_blocks_when_remote_delete_fails(self, mock_create_client):
        client = MagicMock()
        client.delete_style.return_value = {'success': False, 'error': 'remote failed'}
        mock_create_client.return_value = client

        style = Style.objects.create(
            geodata_engine=self.engine,
            name='blocked_style',
            title='Blocked Style',
            format='mbstyle',
            file_name='blocked_style.mbstyle',
            file_content='{"version":8,"layers":[]}',
            validation_state='VALID',
            created_by=self.user,
        )
        request = self.request_factory.post('/admin/geodata_providers/style/')
        request.user = self.user
        model_admin = StyleAdmin(Style, self.site)

        with self.assertRaises(DeleteAborted):
            model_admin.delete_model(request, style)

        self.assertTrue(Style.objects.filter(pk=style.pk).exists())

    @patch('tosca_api.apps.geodata_providers.admin.StoreService.delete_store_safe')
    def test_store_admin_delete_model_uses_service(self, mock_delete_store_safe):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_service_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='delete_service_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        mock_delete_store_safe.return_value = {'success': True, 'message': 'deleted'}
        request = self.request_factory.post('/admin/geodata_providers/store/')
        request.user = self.user
        model_admin = StoreAdmin(Store, self.site)

        model_admin.delete_model(request, store)

        mock_delete_store_safe.assert_called_once_with(store)

    @patch('tosca_api.apps.geodata_providers.admin.StoreService.delete_store_safe')
    def test_store_admin_delete_queryset_uses_service(self, mock_delete_store_safe):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_queryset_ws',
            description='desc',
            created_by=self.user,
        )
        first = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='delete_queryset_store_1',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        second = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='delete_queryset_store_2',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        mock_delete_store_safe.return_value = {'success': True, 'message': 'deleted'}
        request = self.request_factory.post('/admin/geodata_providers/store/')
        request.user = self.user
        model_admin = StoreAdmin(Store, self.site)

        model_admin.delete_queryset(request, Store.objects.filter(pk__in=[first.pk, second.pk]))

        self.assertEqual(mock_delete_store_safe.call_count, 2)

    @patch('tosca_api.apps.geodata_providers.admin.EngineClientFactory.create_client')
    def test_store_admin_uses_geoserver_probe_for_access_badge(self, mock_create_client):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='probe_ws',
            description='desc',
            created_by=self.user,
        )
        store = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='probe_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        client = MagicMock()
        client.probe_store_access.return_value = {
            'success': True,
            'status': 'usable',
            'featuretype_count': 2,
        }
        mock_create_client.return_value = client
        model_admin = StoreAdmin(Store, self.site)

        badge = model_admin.geoserver_access_badge(store)

        client.probe_store_access.assert_called_once_with('probe_ws', 'probe_store')
        self.assertIn('GeoServer OK', badge)

    @patch('tosca_api.apps.geodata_providers.admin_views.store.messages.warning')
    @patch('tosca_api.apps.geodata_providers.admin_views.store.messages.success')
    @patch('tosca_api.apps.geodata_providers.admin_views.store.StoreService.clone_store')
    def test_store_clone_view_uses_service(self, mock_clone_store, mock_success, mock_warning):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='clone_ws',
            description='desc',
            created_by=self.user,
        )
        source = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='source_clone_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        cloned = Store.objects.create(
            workspace=workspace,
            geodata_engine=self.engine,
            name='cloned_store_result',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        mock_clone_store.return_value = {
            'success': True,
            'resource': cloned,
            'sync_result': {'success': True, 'errors': []},
        }
        request = self.request_factory.post(
            f'/admin/geodata_providers/store/{source.pk}/clone/',
            data={
                'name': 'cloned_store',
                'workspace': str(workspace.pk),
                'description': 'desc',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'schema': 'public',
                'username': 'postgres',
                'password': 'secret',
            },
        )
        request.user = User.objects.create_user(
            username='clone-admin',
            email='clone@example.com',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )

        response = store_clone_view(request, source.pk)

        self.assertEqual(response.status_code, 302)
        mock_clone_store.assert_called_once()
        self.assertFalse(mock_clone_store.call_args.kwargs['clone_layers'])
        mock_success.assert_called()
        mock_warning.assert_not_called()


class LayerAdminAjaxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='layeradmin',
            email='layeradmin@example.com',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.engine = GeodataEngine.objects.create(
            name='Layer Engine',
            description='test',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='layer_ws',
            description='desc',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='layer_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        self.request_factory = RequestFactory()

    def _build_request(self):
        request = self.request_factory.get('/admin/geodata_providers/layer/tables-for-store/')
        request.user = self.user
        request.GET = QueryDict(
            f'store_id={self.store.pk}&workspace_id={self.workspace.pk}'
        )
        return request

    @patch('tosca_api.apps.geodata_providers.admin_views.layer.EngineClientFactory.create_client')
    def test_tables_for_store_returns_geoserver_available_without_local_password(self, mock_create_client):
        client = MagicMock()
        client.get_available_featuretypes.return_value = ['roads', 'buildings']
        mock_create_client.return_value = client

        response = tables_for_store_view(self._build_request())

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['tables'], [])
        self.assertEqual(
            payload['geoserver_available'],
            [{'table_name': 'roads'}, {'table_name': 'buildings'}],
        )
        self.assertIn('Set connection credentials first', payload['warning'])

    @patch('tosca_api.apps.geodata_providers.admin_views.layer.get_geometry_tables')
    @patch('tosca_api.apps.geodata_providers.admin_views.layer.EngineClientFactory.create_client')
    @patch.object(Store, 'decrypted_password', new=property(lambda self: 'secret'))
    def test_tables_for_store_merges_geoserver_only_items(self, mock_create_client, mock_get_geometry_tables):
        client = MagicMock()
        client.get_available_featuretypes.return_value = ['roads', 'buildings']
        mock_create_client.return_value = client
        mock_get_geometry_tables.return_value = [
            {
                'table_name': 'roads',
                'geometry_column': 'geom',
                'geometry_type': 'MultiLineString',
                'srid': 3857,
            }
        ]

        response = tables_for_store_view(self._build_request())

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(len(payload['tables']), 1)
        self.assertEqual(payload['geoserver_available'], [{'table_name': 'buildings'}])


class LayerDeleteBehaviorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='layerdelete',
            email='layerdelete@example.com',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.engine = GeodataEngine.objects.create(
            name='Delete Engine',
            description='test',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='delete_ws',
            description='desc',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='delete_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        self.layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='apotheken',
            title='Apotheken',
            table_name='apotheken',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        self.site = AdminSite()
        self.request_factory = RequestFactory()

    @patch('tosca_api.apps.geodata_providers.admin.LayerService.delete_layer_safe')
    def test_layer_delete_model_allows_remote_already_deleted(self, mock_delete_layer_safe):
        mock_delete_layer_safe.return_value = {
            'success': True,
            'already_deleted': True,
            'message': "Layer 'apotheken' was already absent in GeoServer.",
        }

        request = self.request_factory.post(f'/admin/geodata_providers/layer/{self.layer.pk}/delete/')
        request.user = self.user
        model_admin = LayerAdmin(Layer, self.site)
        model_admin.message_user = MagicMock()

        model_admin.delete_model(request, self.layer)

        mock_delete_layer_safe.assert_called_once_with(self.layer)
        model_admin.message_user.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin.LayerService.delete_layer_safe')
    def test_layer_delete_queryset_uses_service_for_each_object(self, mock_delete_layer_safe):
        second_layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='hastane',
            title='Hastane',
            table_name='hastane',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        mock_delete_layer_safe.return_value = {'success': True, 'message': 'deleted'}

        request = self.request_factory.post('/admin/geodata_providers/layer/')
        request.user = self.user
        model_admin = LayerAdmin(Layer, self.site)

        model_admin.delete_queryset(request, Layer.objects.filter(pk__in=[self.layer.pk, second_layer.pk]))

        self.assertEqual(mock_delete_layer_safe.call_count, 2)

    @patch('tosca_api.apps.geodata_providers.admin._run_workspace_sync')
    @patch('tosca_api.apps.geodata_providers.admin.LayerService.update_published_metadata')
    def test_layer_save_model_does_not_pull_sync_before_inline_assignments(self, mock_update_metadata, mock_run_workspace_sync):
        request = self.request_factory.post(f'/admin/geodata_providers/layer/{self.layer.pk}/change/')
        request.user = self.user
        model_admin = LayerAdmin(Layer, self.site)
        self.layer.title = 'Updated Apotheken'
        form = MagicMock()
        form.changed_data = ['title']

        model_admin.save_model(request, self.layer, form, change=True)

        mock_update_metadata.assert_called_once_with(
            layer=self.layer,
            title='Updated Apotheken',
            description=self.layer.description,
            description_content=self.layer.description_content,
        )
        mock_run_workspace_sync.assert_not_called()


class LayerSurfaceRefactorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='layersurface',
            email='layersurface@example.com',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.engine = GeodataEngine.objects.create(
            name='Surface Engine',
            description='test',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='surface_ws',
            description='desc',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='surface_store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            description='desc',
            created_by=self.user,
        )
        self.layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='surface_layer',
            title='Surface Layer',
            description='desc',
            table_name='surface_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        self.request_factory = RequestFactory()
        self.site = AdminSite()

    @patch('tosca_api.apps.geodata_providers.admin_actions.layer.LayerService.publish_existing_layer')
    def test_publish_action_uses_layer_service(self, mock_publish_existing_layer):
        self.layer.publishing_state = 'DRAFT'
        self.layer.save(update_fields=['publishing_state'])
        mock_publish_existing_layer.return_value = {'success': True, 'message': 'published'}
        model_admin = LayerAdmin(Layer, self.site)
        model_admin.message_user = MagicMock()
        request = self.request_factory.post('/admin/geodata_providers/layer/')
        request.user = self.user

        publish_layer(model_admin, request, Layer.objects.filter(pk=self.layer.pk))

        mock_publish_existing_layer.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin_actions.layer.LayerService.unpublish_layer')
    def test_unpublish_action_uses_layer_service(self, mock_unpublish_layer):
        mock_unpublish_layer.return_value = {'success': True, 'message': 'unpublished'}
        model_admin = LayerAdmin(Layer, self.site)
        model_admin.message_user = MagicMock()
        request = self.request_factory.post('/admin/geodata_providers/layer/')
        request.user = self.user

        unpublish_layer(model_admin, request, Layer.objects.filter(pk=self.layer.pk))

        mock_unpublish_layer.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.admin_views.layer.messages.warning')
    @patch('tosca_api.apps.geodata_providers.admin_views.layer.messages.success')
    @patch('tosca_api.apps.geodata_providers.admin_views.layer.EngineClientFactory.create_sync_service')
    @patch('tosca_api.apps.geodata_providers.admin_views.layer.LayerService.publish_postgis')
    def test_publish_postgis_view_uses_layer_service(self, mock_publish_postgis, mock_create_sync_service, mock_success, mock_warning):
        mock_publish_postgis.return_value = {
            'success': True,
            'created': True,
            'message': 'published',
            'resource': self.layer,
        }
        mock_create_sync_service.return_value.sync_layers_for_workspace.return_value = {'errors': []}
        request = self.request_factory.post(
            '/admin/geodata_providers/layer/publish-postgis/',
            data={
                'workspace': str(self.workspace.pk),
                'store': str(self.store.pk),
                'table_name': 'surface_layer',
                'layer_name': 'surface_layer',
                'title': 'Surface Layer',
                'description': 'desc',
                'geometry_column': 'geom',
                'geometry_type': 'Point',
                'srid': 4326,
            },
        )
        request.user = self.user

        response = publish_postgis_view(request)

        self.assertEqual(response.status_code, 302)
        mock_publish_postgis.assert_called_once()
        mock_success.assert_called()
        mock_warning.assert_not_called()


class GeoServerClientDeleteLayerTests(TestCase):
    def test_delete_layer_treats_404_as_already_deleted(self):
        client = GeoServerClient.__new__(GeoServerClient)
        client._client = MagicMock()

        class NotFoundError(Exception):
            status = 404

        client._client.delete_layer.side_effect = NotFoundError('missing')

        result = client.delete_layer('demo', 'roads')

        self.assertTrue(result['success'])
        self.assertTrue(result['already_deleted'])

    def test_publish_featuretype_uses_layer_name_and_native_table_name(self):
        client = GeoServerClient.__new__(GeoServerClient)
        client.url = 'http://example.com/geoserver'
        client._client = MagicMock()
        client._client._requests.return_value = MagicMock(status_code=201, text='created')
        client._client.edit_featuretype.return_value = 200

        result = client.publish_featuretype(
            store_name='demo_store',
            workspace='demo_ws',
            pg_table='apotheken',
            layer_name='apotheken_v2',
            title='Apotheken V2',
            srid=4326,
        )

        self.assertTrue(result['success'])
        request_call = client._client._requests.call_args
        self.assertEqual(request_call.args[0], 'post')
        self.assertIn('/rest/workspaces/demo_ws/datastores/demo_store/featuretypes', request_call.args[1])
        payload = request_call.kwargs['data']
        self.assertIn('<name>apotheken_v2</name>', payload)
        self.assertIn('<nativeName>apotheken</nativeName>', payload)
        self.assertIn('<title>Apotheken V2</title>', payload)

    def test_verify_featuretype_metadata_detects_mismatch(self):
        client = GeoServerClient.__new__(GeoServerClient)
        client.get_featuretype_detail = MagicMock(return_value={
            'name': 'apotheken_v2',
            'title': 'Wrong Title',
            'native_name': 'apotheken',
            'abstract': '',
            'advertised': True,
        })

        result = client.verify_featuretype_metadata(
            workspace='demo_ws',
            store_name='demo_store',
            featuretype_name='apotheken_v2',
            expected_title='Apotheken V2',
            expected_abstract='Expected abstract',
        )

        self.assertFalse(result['verified'])
        self.assertIn('title', result['mismatches'])
        self.assertIn('abstract', result['mismatches'])
