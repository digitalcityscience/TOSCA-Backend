import json
import logging
import os

from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django import forms
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.utils.html import format_html
from django.utils import timezone
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import path, reverse
from .admin_actions import (
    clone_store,
    deactivate_engines,
    publish_layer,
    reactivate_engines,
    set_as_default,
    sync_engines,
    sync_workspaces,
    test_connection,
    unpublish_layer,
)
from .admin_views import (
    engine_deactivate_view, engine_force_delete_view, engine_reactivate_view,
    engine_test_connection_view, engine_sync_view,
    workspace_sync_view,
    store_postgis_tables_view, store_clone_view,
    publish_postgis_view, stores_for_workspace_view, tables_for_store_view,
)
from .engine_factory import EngineClientFactory
from .exceptions import GeoServerConnectionError, GeodataEngineError
from .models import GeodataEngine, Workspace, Store, Layer, Style, LayerStyleAssignment
from .services.commands.geodata_engine_service import GeodataEngineService
from .services.commands.layer_service import LayerService
from .services.commands.store_service import StoreService
from .services.commands.style_validation_service import StyleValidationService
from .services.commands.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

_GEODATA_PROVIDER_ADMIN_ORDER = {
    'GeodataEngine': 0,
    'Workspace': 1,
    'Store': 2,
    'Layer': 3,
    'Style': 4,
}

_GEODATA_PROVIDER_ADMIN_LABELS = {
    'GeodataEngine': 'Geodata Provider',
    'Workspace': 'Workspace',
    'Store': 'Store',
    'Layer': 'Layer',
    'Style': 'Style',
}

_ADMIN_APP_ORDER = {
    'geocontext': 0,
    'geostories': 1,
}


def _patch_geodata_providers_admin_app_list():
    original_get_app_list = admin.site.get_app_list

    def get_app_list(request, app_label=None):
        app_list = original_get_app_list(request, app_label=app_label)

        app_list.sort(
            key=lambda app: (
                _ADMIN_APP_ORDER.get(app.get('app_label'), 999),
                app.get('name', ''),
            )
        )

        for app in app_list:
            if app.get('app_label') != 'geodata_providers':
                continue

            app['models'].sort(
                key=lambda model: (
                    _GEODATA_PROVIDER_ADMIN_ORDER.get(model.get('object_name'), 999),
                    model.get('name', ''),
                )
            )

            for model in app['models']:
                label = _GEODATA_PROVIDER_ADMIN_LABELS.get(model.get('object_name'))
                if label:
                    model['name'] = label

        return app_list

    if getattr(admin.site.get_app_list, '__name__', '') != 'get_app_list':
        return

    admin.site.get_app_list = get_app_list


class DeleteAborted(Exception):
    """Abort admin delete flow without turning it into a 403."""

    def __init__(self, message):
        self.message = message
        super().__init__(message)


def _admin_change_url(obj):
    return reverse(
        f'admin:{obj._meta.app_label}_{obj._meta.model_name}_change',
        args=[obj.pk],
    )


class RemoteDeleteAdminMixin:
    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context=extra_context)
        except DeleteAborted as exc:
            self.message_user(request, exc.message, messages.ERROR)
            try:
                obj = self.get_object(request, object_id)
            except Exception:
                obj = None
            if obj is not None:
                return redirect(_admin_change_url(obj))
            return redirect(reverse(f'admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist'))


def _message_sync_result(modeladmin, request, label, result):
    if result.get('skipped'):
        modeladmin.message_user(
            request,
            f"{label} sync skipped: {result.get('reason', 'not requested')}.",
            messages.INFO,
        )
        return

    if result.get('success'):
        modeladmin.message_user(
            request,
            f"{label} sync completed.",
            messages.SUCCESS,
        )
        return

    modeladmin.message_user(
        request,
        f"{label} sync failed: {result.get('error', 'unknown error')}",
        messages.WARNING,
    )


def _run_engine_sync(modeladmin, request, engine):
    if not engine:
        return
    try:
        service = EngineClientFactory.create_sync_service(engine)
        result = service.sync_all_resources(created_by=request.user)
        _message_sync_result(modeladmin, request, f"Engine '{engine.name}'", result)
    except (GeoServerConnectionError, GeodataEngineError) as exc:
        modeladmin.message_user(
            request,
            f"Engine '{engine.name}' sync failed: {exc}",
            messages.WARNING,
        )


def _run_workspace_sync(modeladmin, request, workspace):
    engine = workspace.geodata_engine if workspace else None
    if not engine:
        return

    try:
        service = EngineClientFactory.create_sync_service(engine)
        store_result = service.sync_stores_for_workspace(workspace, created_by=request.user)
        style_result = service.sync_styles_for_scope(workspace, created_by=request.user)
        layer_result = service.sync_layers_for_workspace(workspace, created_by=request.user)
    except (GeoServerConnectionError, GeodataEngineError) as exc:
        modeladmin.message_user(
            request,
            f"Workspace '{workspace.name}' sync failed: {exc}",
            messages.WARNING,
        )
        return

    if store_result.get('errors') or style_result.get('errors') or layer_result.get('errors'):
        errors = (
            store_result.get('errors', [])
            + style_result.get('errors', [])
            + layer_result.get('errors', [])
        )
        modeladmin.message_user(
            request,
            f"Workspace '{workspace.name}' sync completed with issues: {' | '.join(errors[:2])}",
            messages.WARNING,
        )
        return

    modeladmin.message_user(
        request,
        f"Workspace '{workspace.name}' sync completed.",
        messages.SUCCESS,
    )


# Admin Forms
class GeodataEngineForm(forms.ModelForm):
    class Meta:
        model = GeodataEngine
        fields = '__all__'
        widgets = {
            'admin_password': forms.PasswordInput(render_value=False),
            'api_key': forms.PasswordInput(render_value=False),
            'base_url': forms.TextInput(attrs={'size': 60}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        engine_type = cleaned_data.get('engine_type') or self.instance.engine_type
        if engine_type != 'geoserver':
            return cleaned_data

        password = cleaned_data.get('admin_password')
        if not self.instance._state.adding and not password:
            password = self.instance.decrypted_admin_password

        temp_engine = GeodataEngine(
            name=cleaned_data.get('name') or self.instance.name,
            description=cleaned_data.get('description') or self.instance.description,
            engine_type=engine_type,
            base_url=cleaned_data.get('base_url') or self.instance.base_url,
            admin_username=cleaned_data.get('admin_username') or self.instance.admin_username,
            admin_password=password or '',
            api_key=cleaned_data.get('api_key') or self.instance.api_key,
            is_active=cleaned_data.get('is_active', self.instance.is_active),
            is_default=cleaned_data.get('is_default', self.instance.is_default),
        )

        try:
            client = EngineClientFactory.create_client(temp_engine)
            client.validate_connection()
        except (GeoServerConnectionError, GeodataEngineError) as exc:
            raise ValidationError(f'Engine connection could not be validated: {exc}')

        return cleaned_data


class WorkspaceAdminForm(forms.ModelForm):
    class Meta:
        model = Workspace
        fields = '__all__'
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        error = WorkspaceService.validate_workspace_name(name)
        if error:
            raise ValidationError(error)
        return name

    def _post_clean(self):
        super()._post_clean()
        if self.errors or not self.instance._state.adding:
            return

        engine = self.cleaned_data.get('geodata_engine')
        name = self.cleaned_data.get('name')
        if not engine or not name:
            return
        if Workspace.objects.filter(geodata_engine=engine, name=name).exists():
            self.add_error('name', 'Workspace with this provider and name already exists.')


# GeodataEngine Admin - Engine Management
@admin.register(GeodataEngine)
class GeodataEngineAdmin(RemoteDeleteAdminMixin, admin.ModelAdmin):
    form = GeodataEngineForm
    change_form_template = 'admin/geodata_providers/geodataengine/change_form.html'
    list_display = [
        'name', 'engine_type', 'base_url',
        'is_active', 'is_default',
        'connection_status_badge', 'workspace_count', 'style_count',
        'layer_count', 'active_layer_settings_count',
    ]
    list_filter = ['engine_type', 'is_active', 'is_default']
    search_fields = ['name', 'base_url']
    readonly_fields = ['id', 'created_at', 'updated_at']
    list_per_page = 25
    actions = [sync_engines, test_connection, set_as_default, deactivate_engines, reactivate_engines]

    fieldsets = (
        ('Identity', {
            'fields': ('name', 'description', 'engine_type'),
        }),
        ('Connection', {
            'fields': ('base_url', 'admin_username', 'admin_password', 'api_key'),
        }),
        ('State', {
            'fields': ('is_active', 'is_default'),
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    # ------------------------------------------------------------------
    # 1.4.2 — Custom URLs wired for AJAX endpoints
    # ------------------------------------------------------------------
    def get_urls(self):
        custom = [
            path(
                '<uuid:engine_id>/test-connection/',
                self.admin_site.admin_view(engine_test_connection_view),
                name='geodataengine_test_connection',
            ),
            path(
                '<uuid:engine_id>/sync/',
                self.admin_site.admin_view(engine_sync_view),
                name='geodataengine_sync',
            ),
            path(
                '<uuid:engine_id>/deactivate/',
                self.admin_site.admin_view(engine_deactivate_view),
                name='geodataengine_deactivate',
            ),
            path(
                '<uuid:engine_id>/reactivate/',
                self.admin_site.admin_view(engine_reactivate_view),
                name='geodataengine_reactivate',
            ),
            path(
                '<uuid:engine_id>/force-delete/',
                self.admin_site.admin_view(engine_force_delete_view),
                name='geodataengine_force_delete',
            ),
        ]
        return custom + super().get_urls()

    # ------------------------------------------------------------------
    # Queryset — annotate counts so list columns are DB-efficient
    # ------------------------------------------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _workspace_count=Count('workspaces', distinct=True),
            _style_count=Count('styles', distinct=True),
            _layer_count=Count('workspaces__layers', distinct=True),
            _active_layer_settings_count=Count(
                'workspaces__layers__style_assignments',
                filter=Q(workspaces__layers__style_assignments__is_active=True),
                distinct=True,
            ),
        )

    # ------------------------------------------------------------------
    # Computed list_display columns (tasks 1.2.1 – 1.2.3)
    # ------------------------------------------------------------------
    def workspace_count(self, obj):
        return obj._workspace_count
    workspace_count.short_description = 'Workspaces'
    workspace_count.admin_order_field = '_workspace_count'

    def layer_count(self, obj):
        return obj._layer_count
    layer_count.short_description = 'Layers'
    layer_count.admin_order_field = '_layer_count'

    def style_count(self, obj):
        return obj._style_count
    style_count.short_description = 'Styles'
    style_count.admin_order_field = '_style_count'

    def active_layer_settings_count(self, obj):
        return obj._active_layer_settings_count
    active_layer_settings_count.short_description = 'Active Layer Settings'
    active_layer_settings_count.admin_order_field = '_active_layer_settings_count'

    def connection_status_badge(self, obj):
        """
        Static badge — live check is done via 'Test Connection' action (1.3.3)
        and the change-form button (1.4). Never fires a network request on
        list load.
        """
        if obj.is_active:
            return format_html(
                '<span style="color:#3ecf8e;font-weight:600;">&#10003; Active</span>'
            )
        return format_html(
            '<span style="color:#e54d4d;font-weight:600;">&#10007; Inactive</span>'
        )
    connection_status_badge.short_description = 'Status'
    connection_status_badge.admin_order_field = 'is_active'

    # ------------------------------------------------------------------
    # Save / exclude helpers
    # ------------------------------------------------------------------
    def save_model(self, request, obj, form, change):
        engine_fields = GeodataEngineService.ENGINE_FIELDS

        if change:
            update_data = {
                field: form.cleaned_data[field]
                for field in form.changed_data
                if field in engine_fields and field in form.cleaned_data
            }
            if 'admin_password' in update_data and not update_data['admin_password']:
                update_data.pop('admin_password')
            if 'api_key' in update_data and not update_data['api_key']:
                update_data.pop('api_key')
            engine, _sync_result = GeodataEngineService.update_engine(
                obj,
                user=request.user,
                **update_data,
            )
        else:
            create_data = {
                field: form.cleaned_data[field]
                for field in engine_fields
                if field in form.cleaned_data
            }
            engine, _sync_result = GeodataEngineService.create_engine(
                user=request.user,
                **create_data,
            )

        obj.__dict__.update(engine.__dict__)
        _message_sync_result(self, request, f"Engine '{engine.name}'", _sync_result)

    def get_exclude(self, request, obj=None):
        """Hide auto-managed fields from form."""
        return ['created_by']

    def delete_model(self, request, obj):
        result = GeodataEngineService.delete_engine_safe(obj)
        if not result.get('success'):
            raise DeleteAborted(result['message'])

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            result = GeodataEngineService.delete_engine_safe(obj)
            if not result.get('success'):
                self.message_user(
                    request,
                    f"Engine '{obj.name}' NOT deleted: {result['message']}",
                    messages.ERROR,
                )
                continue
            self.message_user(
                request,
                result['message'],
                messages.SUCCESS,
            )
    
# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Workspace Admin
# ──────────────────────────────────────────────────────────────────────────────

class StoreInline(admin.TabularInline):
    """Read-only store preview inside the workspace change form (task 2.2)."""
    model = Store
    fields = ('name', 'store_type', 'host', 'schema', 'has_credential')
    readonly_fields = ('name', 'store_type', 'host', 'schema', 'has_credential')
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_credential(self, obj):
        if bool(obj.password):
            return format_html(
                '<span style="color:#3ecf8e;font-weight:600;">&#10003;</span>'
            )
        return format_html(
            '<span style="color:#e54d4d;font-weight:600;">&#10007;</span>'
        )
    has_credential.short_description = 'Credential'


@admin.register(Workspace)
class WorkspaceAdmin(RemoteDeleteAdminMixin, admin.ModelAdmin):
    form = WorkspaceAdminForm
    change_form_template = 'admin/geodata_providers/workspace/change_form.html'
    list_display = ['name', 'engine_link', 'description', 'store_count', 'layer_count', 'created_at']
    list_filter = ['geodata_engine', 'geodata_engine__engine_type']
    search_fields = ['name', 'geodata_engine__name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [StoreInline]
    actions = [sync_workspaces]
    list_per_page = 25

    fieldsets = (
        ('Identity', {
            'fields': ('geodata_engine', 'name', 'description'),
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    # ------------------------------------------------------------------
    # 2.3.2 — Custom URL for the Sync Workspace AJAX endpoint
    # ------------------------------------------------------------------
    def get_urls(self):
        custom = [
            path(
                '<uuid:workspace_id>/sync/',
                self.admin_site.admin_view(workspace_sync_view),
                name='workspace_sync',
            ),
        ]
        return custom + super().get_urls()

    # ------------------------------------------------------------------
    # Queryset — annotate store + layer counts (tasks 2.1.6)
    # ------------------------------------------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('geodata_engine')
        return qs.annotate(
            _store_count=Count('stores', distinct=True),
            _layer_count=Count('layers', distinct=True),
        )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if obj:  # lock engine when editing existing
            readonly += ['geodata_engine', 'name']
        return readonly

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'geodata_engine' in form.base_fields:
            form.base_fields['geodata_engine'].queryset = (
                GeodataEngine.objects.filter(is_active=True).order_by('name')
            )
        return form

    # ------------------------------------------------------------------
    # Computed list_display columns (tasks 2.1.5, 2.1.6)
    # ------------------------------------------------------------------
    def engine_link(self, obj):
        if not obj.geodata_engine:
            return '—'
        return format_html('<a href="{}">{}</a>', _admin_change_url(obj.geodata_engine), obj.geodata_engine.name)
    engine_link.short_description = 'Engine'
    engine_link.admin_order_field = 'geodata_engine__name'

    def store_count(self, obj):
        return obj._store_count
    store_count.short_description = 'Stores'
    store_count.admin_order_field = '_store_count'

    def layer_count(self, obj):
        return obj._layer_count
    layer_count.short_description = 'Layers'
    layer_count.admin_order_field = '_layer_count'

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def save_model(self, request, obj, form, change):
        logger.info(
            "WorkspaceAdmin.save_model start: change=%s name=%s engine_id=%s",
            change,
            obj.name,
            obj.geodata_engine_id,
        )
        if not change:
            result = WorkspaceService.create_workspace(
                engine=form.cleaned_data.get('geodata_engine'),
                name=form.cleaned_data.get('name', obj.name),
                description=form.cleaned_data.get('description', obj.description),
                user=request.user,
            )
            if not result.get('success'):
                raise ValidationError(result.get('message', 'Workspace create failed.'))
            workspace = result['resource']
            obj.__dict__.update(workspace.__dict__)
        else:
            with transaction.atomic():
                super().save_model(request, obj, form, change)
        logger.info(
            "WorkspaceAdmin.save_model completed: pk=%s",
            obj.pk,
        )
        _run_workspace_sync(self, request, obj)

    # ------------------------------------------------------------------
    # Delete safety (task 2.4) — engine-first, never delete Django if GeoServer fails
    # ------------------------------------------------------------------
    def delete_model(self, request, obj):
        result = WorkspaceService.delete_workspace_safe(obj)
        if not result.get('success'):
            raise DeleteAborted(result.get('message', f"Cannot delete workspace '{obj.name}'."))
        if result.get('already_deleted'):
            self.message_user(
                request,
                f"Workspace '{obj.name}' was already absent in GeoServer. Django record was removed.",
                messages.WARNING,
            )

    def delete_queryset(self, request, queryset):
        for obj in queryset.select_related('geodata_engine'):
            result = WorkspaceService.delete_workspace_safe(obj)
            if not result.get('success'):
                self.message_user(
                    request,
                    result.get('message', f"Workspace '{obj.name}' NOT deleted."),
                    messages.ERROR,
                )
            elif result.get('already_deleted'):
                self.message_user(
                    request,
                    f"Workspace '{obj.name}' was already absent in GeoServer. Django record was removed.",
                    messages.WARNING,
                )
    
# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 — Store Admin
# ──────────────────────────────────────────────────────────────────────────────

class StoreAdminForm(forms.ModelForm):
    """Store form — password widget never pre-populates (task 3.2.3)."""
    class Meta:
        model = Store
        fields = '__all__'
        widgets = {
            'password': forms.PasswordInput(
                render_value=False,
                attrs={'placeholder': 'Leave blank to keep existing password.'},
            ),
            'description': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'password': 'Leave blank to keep existing password.',
        }

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        workspace = cleaned_data.get('workspace') or self.instance.workspace
        store_type = cleaned_data.get('store_type') or self.instance.store_type
        engine = workspace.geodata_engine if workspace else None

        if not self.instance._state.adding:
            if engine and store_type == 'postgis':
                blocked_fields = ['host', 'port', 'database', 'username', 'schema']
                changed_fields = [
                    field for field in blocked_fields
                    if cleaned_data.get(field) != getattr(self.instance, field)
                ]
                if cleaned_data.get('password'):
                    changed_fields.append('password')
                if changed_fields:
                    raise ValidationError(
                        "Updating an existing store's remote connection fields is not supported yet. "
                        "Recreate the store or use an explicit sync-safe flow."
                    )
        return cleaned_data

    def _post_clean(self):
        super()._post_clean()
        if self.errors or not self.instance._state.adding:
            return

        workspace = self.cleaned_data.get('workspace')
        store_type = self.cleaned_data.get('store_type')
        engine = workspace.geodata_engine if workspace else None

        if not engine or store_type != 'postgis':
            return

        password = self.cleaned_data.get('password')
        if not password:
            self.add_error('password', 'Password is required to create a PostGIS store in the engine.')
            return

        try:
            client = EngineClientFactory.create_client(engine)
            result = client.create_postgis_store(
                name=self.cleaned_data.get('name'),
                workspace=workspace.name,
                host=self.cleaned_data.get('host'),
                port=self.cleaned_data.get('port') or 5432,
                database=self.cleaned_data.get('database'),
                username=self.cleaned_data.get('username'),
                password=password,
                schema=self.cleaned_data.get('schema') or 'public',
            )
        except (GeoServerConnectionError, GeodataEngineError) as exc:
            self.add_error(None, f"Store could not be created in engine: {exc}")
            return

        if not result.get('success'):
            self.add_error(
                None,
                result.get('error') or result.get('message') or 'Store create failed in engine.',
            )
            return

        verification = client.post_verify_store(
            workspace.name,
            self.cleaned_data.get('name'),
            expected_exists=True,
            expected_details={
                'host': self.cleaned_data.get('host'),
                'port': self.cleaned_data.get('port') or 5432,
                'database': self.cleaned_data.get('database'),
                'username': self.cleaned_data.get('username'),
                'schema': self.cleaned_data.get('schema') or 'public',
            },
        )
        if not verification.get('verified'):
            self.add_error(
                None,
                verification.get('message') or 'Store creation could not be verified in engine.',
            )


class StyleAdminForm(forms.ModelForm):
    upload_file = forms.FileField(
        required=False,
        label='Upload file',
        help_text='Upload .sld, .json, or .mbstyle. Pasted content below is also supported.',
    )

    class Meta:
        model = Style
        fields = (
            'geodata_engine', 'workspace', 'name', 'title', 'description',
            'format', 'upload_file', 'file_name', 'file_content',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'file_content': forms.Textarea(attrs={'rows': 18, 'style': 'font-family:monospace;'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        engine = cleaned_data.get('geodata_engine') or self.instance.geodata_engine
        workspace = cleaned_data.get('workspace') or self.instance.workspace
        if workspace and engine and workspace.geodata_engine_id != engine.id:
            raise ValidationError('Style workspace must belong to the selected geodata engine.')

        upload_file = cleaned_data.get('upload_file')
        if upload_file:
            content_bytes = upload_file.read()
            cleaned_data['file_content'] = content_bytes.decode('utf-8')
            cleaned_data['file_name'] = upload_file.name

        if self.instance.pk and not cleaned_data.get('file_content'):
            cleaned_data['file_content'] = self.instance.file_content
        if self.instance.pk and not cleaned_data.get('file_name'):
            cleaned_data['file_name'] = self.instance.file_name
        if self.instance.pk and not cleaned_data.get('format'):
            cleaned_data['format'] = self.instance.format

        file_name = cleaned_data.get('file_name') or ''
        style_format = cleaned_data.get('format') or self._infer_format(file_name)
        if style_format:
            cleaned_data['format'] = style_format

        content = cleaned_data.get('file_content') or ''
        if self.instance._state.adding and not content:
            raise ValidationError({
                'file_content': 'Upload a style file or paste style content before saving.'
            })
        if content:
            validation = StyleValidationService.validate(
                content=content,
                style_format=style_format,
            )
            self.instance.validation_state = 'VALID' if validation.get('valid') else 'INVALID'
            self.instance.validation_errors = validation.get('errors', [])
            if not validation.get('valid'):
                raise ValidationError({
                    'file_content': 'Style validation failed: ' + ' | '.join(validation.get('errors', []))
                })
        return cleaned_data

    @staticmethod
    def _infer_format(file_name: str) -> str:
        ext = os.path.splitext(file_name)[1].lower()
        if ext == '.sld':
            return 'sld'
        if ext in {'.json', '.mbstyle'}:
            return 'mbstyle'
        return ''


class LayerStyleAssignmentAdminForm(forms.ModelForm):
    class Meta:
        model = LayerStyleAssignment
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        layer = cleaned_data.get('layer') or self.instance.layer
        style = cleaned_data.get('style') or self.instance.style
        if not layer or not style:
            return cleaned_data
        if style.validation_state == 'INVALID':
            raise ValidationError('Invalid styles cannot be assigned to layers.')
        return cleaned_data


class StoreCloneForm(forms.Form):
    """Form used by the clone-store admin view."""
    name = forms.CharField(
        max_length=100,
        label='New store name',
        help_text='Must be unique within the target workspace.',
    )
    workspace = forms.ModelChoiceField(
        queryset=Workspace.objects.select_related('geodata_engine').order_by('geodata_engine__name', 'name'),
        label='Target workspace',
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Description',
    )
    host = forms.CharField(max_length=255, required=False, label='DB Host')
    port = forms.IntegerField(initial=5432, required=False, label='DB Port')
    database = forms.CharField(max_length=100, required=False, label='Database')
    schema = forms.CharField(max_length=100, initial='public', required=False, label='Schema')
    username = forms.CharField(max_length=100, required=False, label='DB Username')
    password = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        label='DB Password',
        help_text='Required to create the store in GeoServer. Not cloned from source.',
    )


class LayerInline(admin.TabularInline):
    """Read-only layer preview inside the store change form (task 3.4)."""
    model = Layer
    fields = ('name', 'title', 'geometry_type', 'srid', 'publishing_state')
    readonly_fields = ('name', 'title', 'geometry_type', 'srid', 'publishing_state')
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class NoCredentialFilter(SimpleListFilter):
    title = 'Password Status'
    parameter_name = 'password_status'

    def lookups(self, request, model_admin):
        return [
            ('missing', 'Missing Password'),
            ('set', 'Password Set'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'missing':
            return queryset.filter(password='')
        if self.value() == 'set':
            return queryset.exclude(password='')
        return queryset


@admin.register(Store)
class StoreAdmin(RemoteDeleteAdminMixin, admin.ModelAdmin):
    form = StoreAdminForm
    actions = [clone_store]
    list_display = [
        'name', 'provider_link', 'workspace_link', 'store_type',
        'host', 'schema', 'geoserver_access_badge', 'layer_count',
    ]
    list_filter = ['store_type', 'workspace__geodata_engine', 'workspace', NoCredentialFilter]
    search_fields = ['name', 'workspace__name', 'host', 'schema']
    readonly_fields = ['provider_link', 'id', 'created_at', 'updated_at']
    inlines = [LayerInline]
    list_per_page = 25
    change_form_template = 'admin/geodata_providers/store/change_form.html'

    fieldsets = (
        ('Identity', {
            'fields': ('name', 'provider_link', 'workspace', 'store_type', 'description'),
        }),
        ('PostGIS Connection', {
            'fields': ('host', 'port', 'database', 'username', 'password', 'schema'),
            'description': 'Connection credentials for PostGIS stores.',
        }),
        ('File-based Configuration', {
            'fields': ('file_path', 'charset'),
            'classes': ('collapse',),
            'description': 'Required for file-based and GeoTIFF stores.',
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    # ------------------------------------------------------------------
    # 3.3.2 + clone URL — Custom URLs wired for AJAX/form endpoints
    # ------------------------------------------------------------------
    def get_urls(self):
        custom = [
            path(
                '<uuid:store_id>/postgis-tables/',
                self.admin_site.admin_view(store_postgis_tables_view),
                name='store_postgis_tables',
            ),
            path(
                '<uuid:store_id>/clone/',
                self.admin_site.admin_view(store_clone_view),
                name='store_clone',
            ),
        ]
        return custom + super().get_urls()

    # ------------------------------------------------------------------
    # Queryset — annotate layer count
    # ------------------------------------------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('workspace__geodata_engine')
        return qs.annotate(_layer_count=Count('layers', distinct=True))

    # ------------------------------------------------------------------
    # Lock Identity fields after creation (task 3.2.1)
    # ------------------------------------------------------------------
    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if obj:  # editing existing store
            readonly += ['name', 'workspace', 'store_type']
        return readonly

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'workspace' in form.base_fields:
            form.base_fields['workspace'].queryset = (
                Workspace.objects.select_related('geodata_engine')
                .all().order_by('geodata_engine__name', 'name')
            )
        return form

    def render_change_form(self, request, context, add=False, change=False, form_url='', obj=None):
        workspace = obj.workspace if obj else None
        provider = workspace.geodata_engine if workspace else None
        context['provider_context'] = {
            'provider': provider,
            'workspace': workspace,
        }
        if obj and provider:
            context['title'] = f"Change Data Store: {obj.name}"
            context['subtitle'] = f"Provider: {provider.name} | Workspace: {workspace.name if workspace else '—'}"
        return super().render_change_form(request, context, add=add, change=change, form_url=form_url, obj=obj)

    # ------------------------------------------------------------------
    # Computed list_display columns (tasks 3.1.2, 3.1.5)
    # ------------------------------------------------------------------
    def workspace_link(self, obj):
        if not obj.workspace:
            return '—'
        return format_html('<a href="{}">{}</a>', _admin_change_url(obj.workspace), obj.workspace.name)
    workspace_link.short_description = 'Workspace'
    workspace_link.admin_order_field = 'workspace__name'

    def provider_link(self, obj):
        engine = obj.workspace.geodata_engine if obj and obj.workspace else None
        if not engine:
            return '—'
        return format_html('<a href="{}">{}</a>', _admin_change_url(engine), engine.name)
    provider_link.short_description = 'Provider'

    def geoserver_access_badge(self, obj):
        engine = obj.workspace.geodata_engine if obj.workspace else None
        workspace_name = obj.workspace.name if obj.workspace else None
        if not engine or not workspace_name:
            return format_html('<span style="color:#666;font-weight:600;">&#8212; No engine</span>')

        try:
            client = EngineClientFactory.create_client(engine)
            result = client.probe_store_access(workspace_name, obj.name)
        except (GeoServerConnectionError, GeodataEngineError) as exc:
            result = {'success': False, 'message': str(exc)}

        if result.get('success'):
            return format_html(
                '<span style="color:#3ecf8e;font-weight:600;">&#10003; GeoServer OK</span>'
            )

        return format_html(
            '<span style="color:#e54d4d;font-weight:600;">&#10007; GeoServer Error</span>'
        )
    geoserver_access_badge.short_description = 'GeoServer Access'

    def layer_count(self, obj):
        return obj._layer_count
    layer_count.short_description = 'Layers'
    layer_count.admin_order_field = '_layer_count'

    # ------------------------------------------------------------------
    # Save — preserve existing encrypted password if submitted blank (3.2.4)
    # ------------------------------------------------------------------
    def save_model(self, request, obj, form, change):
        if change and not form.cleaned_data.get('password'):
            # Reload the stored encrypted value so we never overwrite with ''
            obj.password = Store.objects.filter(pk=obj.pk).values_list('password', flat=True).first() or ''
        if obj.workspace and not obj.geodata_engine:
            obj.geodata_engine = obj.workspace.geodata_engine
        if not change:
            obj.created_by = request.user
        with transaction.atomic():
            super().save_model(request, obj, form, change)
        if obj.workspace:
            _run_workspace_sync(self, request, obj.workspace)

    def _store_dependency_counts(self, obj):
        return {
            'layers': obj.layers.count(),
        }

    def _ensure_store_can_be_deleted(self, obj):
        counts = self._store_dependency_counts(obj)
        if any(counts.values()):
            details = ", ".join(
                f"{label}={value}" for label, value in counts.items() if value
            )
            raise DeleteAborted(
                f"Cannot delete store '{obj.name}': dependent records exist ({details})."
            )

    # ------------------------------------------------------------------
    # Delete safety (task 3.5) — engine-first
    # ------------------------------------------------------------------
    def delete_model(self, request, obj):
        result = StoreService.delete_store_safe(obj)
        if not result.get('success'):
            raise DeleteAborted(
                f"Cannot delete store '{obj.name}': "
                f"{result.get('error', result.get('message', 'Engine deletion failed.'))}"
            )
        if result.get('already_deleted'):
            self.message_user(
                request,
                f"Store '{obj.name}' was already absent in GeoServer. Django record was removed.",
                messages.WARNING,
            )

    def delete_queryset(self, request, queryset):
        for obj in queryset.select_related('workspace__geodata_engine'):
            result = StoreService.delete_store_safe(obj)
            if not result.get('success'):
                self.message_user(
                    request,
                    f"Store '{obj.name}' NOT deleted — "
                    f"{result.get('error', result.get('message', 'unknown'))}",
                    messages.ERROR,
                )
            else:
                if result.get('already_deleted'):
                    self.message_user(
                        request,
                        f"Store '{obj.name}' was already absent in GeoServer. Django record was removed.",
                        messages.WARNING,
                    )


# ======================================================================
# PHASE 4 — Layer Admin
# ======================================================================


class LayerStyleInline(admin.TabularInline):
    model = LayerStyleAssignment
    form = LayerStyleAssignmentAdminForm
    fields = ('style', 'role', 'is_active', 'created_at')
    readonly_fields = ('created_at',)
    extra = 0
    show_change_link = True


@admin.register(Layer)
class LayerAdmin(RemoteDeleteAdminMixin, admin.ModelAdmin):
    actions = [publish_layer, unpublish_layer]
    change_form_template = 'admin/geodata_providers/layer/change_form.html'
    list_display = [
        'name', 'title', 'workspace_link', 'store_name',
        'geometry_type', 'srid', 'default_style_name', 'publishing_state_badge', 'is_public',
    ]
    list_filter = ['publishing_state', 'geometry_type', 'workspace__geodata_engine', 'workspace', 'store', 'is_public']
    search_fields = ['name', 'title', 'table_name', 'workspace__name']
    readonly_fields = [
        'id', 'name', 'provider_link', 'table_name', 'geometry_column', 'geometry_type',
        'workspace', 'store', 'created_at', 'updated_at', 'publishing_state',
        'published_url', 'publishing_error', 'default_style_display',
        'additional_styles_display', 'available_styles_display', 'selected_styles_display',
    ]
    inlines = [LayerStyleInline]
    list_per_page = 25

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'name', 'provider_link', 'title', 'description'),
        }),
        ('Geometry & CRS', {
            'fields': ('table_name', 'geometry_column', 'geometry_type', 'srid'),
        }),
        ('Source', {
            'fields': ('workspace', 'store'),
        }),
        ('Visibility', {
            'fields': ('publishing_state', 'is_public', 'published_url', 'publishing_error'),
        }),
        ('Layer Settings', {
            'fields': (
                'queryable', 'opaque', 'default_style_display',
                'additional_styles_display', 'available_styles_display',
                'selected_styles_display',
            ),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    # ------------------------------------------------------------------
    # Custom URLs — publish-postgis form + AJAX stores endpoint
    # ------------------------------------------------------------------
    def get_urls(self):
        custom = [
            path(
                'publish-postgis/',
                self.admin_site.admin_view(publish_postgis_view),
                name='layer_publish_postgis',
            ),
            path(
                'stores-for-workspace/',
                self.admin_site.admin_view(stores_for_workspace_view),
                name='layer_stores_for_workspace',
            ),
            path(
                'tables-for-store/',
                self.admin_site.admin_view(tables_for_store_view),
                name='layer_tables_for_store',
            ),
        ]
        return custom + super().get_urls()

    def add_view(self, request, form_url='', extra_context=None):
        self.message_user(
            request,
            "Layer creation is handled via the Publish PostGIS flow.",
            messages.INFO,
        )
        return HttpResponseRedirect(reverse('admin:layer_publish_postgis'))

    # ------------------------------------------------------------------
    # Computed list_display columns
    # ------------------------------------------------------------------
    def workspace_link(self, obj):
        return format_html('<a href="{}">{}</a>', _admin_change_url(obj.workspace), obj.workspace.name)
    workspace_link.short_description = 'Workspace'
    workspace_link.admin_order_field = 'workspace__name'

    def provider_link(self, obj):
        engine = obj.workspace.geodata_engine if obj and obj.workspace else None
        if not engine:
            return '—'
        return format_html('<a href="{}">{}</a>', _admin_change_url(engine), engine.name)
    provider_link.short_description = 'Provider'

    def store_name(self, obj):
        return format_html('<a href="{}">{}</a>', _admin_change_url(obj.store), obj.store.name)
    store_name.short_description = 'Store'
    store_name.admin_order_field = 'store__name'

    def publishing_state_badge(self, obj):
        colours = {
            'PUBLISHED':   ('#3ecf8e', '#fff'),
            'UNPUBLISHED': ('#f5a623', '#fff'),
            'DRAFT':       ('#888',    '#fff'),
            'FAILED':      ('#e54d4d', '#fff'),
        }
        bg, fg = colours.get(obj.publishing_state, ('#ccc', '#333'))
        return format_html(
            '<span style="background:{};color:{};padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">{}</span>',
            bg, fg, obj.publishing_state,
        )
    publishing_state_badge.short_description = 'State'
    publishing_state_badge.admin_order_field = 'publishing_state'

    # ------------------------------------------------------------------
    # Queryset
    # ------------------------------------------------------------------
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'workspace__geodata_engine', 'store',
        ).prefetch_related('style_assignments__style', 'workspace__styles')

    def render_change_form(self, request, context, add=False, change=False, form_url='', obj=None):
        workspace = obj.workspace if obj else None
        provider = workspace.geodata_engine if workspace else None
        store = obj.store if obj else None
        context['provider_context'] = {
            'provider': provider,
            'workspace': workspace,
            'store': store,
        }
        if obj and provider:
            context['title'] = f"Change Layer: {obj.name}"
            context['subtitle'] = (
                f"Provider: {provider.name} | Workspace: {workspace.name if workspace else '—'}"
                f" | Store: {store.name if store else '—'}"
            )
        return super().render_change_form(request, context, add=add, change=change, form_url=form_url, obj=obj)

    def _active_style_assignments(self, obj):
        return [
            assignment for assignment in obj.style_assignments.all()
            if assignment.is_active
        ]

    def default_style_name(self, obj):
        assignment = next(
            (
                item for item in self._active_style_assignments(obj)
                if item.role == 'default'
            ),
            None,
        )
        return assignment.style.qualified_name if assignment else '—'
    default_style_name.short_description = 'Default Style'

    def default_style_display(self, obj):
        return self.default_style_name(obj)
    default_style_display.short_description = 'Default Style'

    def additional_styles_display(self, obj):
        names = [
            assignment.style.qualified_name
            for assignment in self._active_style_assignments(obj)
            if assignment.role == 'alternate'
        ]
        return ', '.join(names) if names else '—'
    additional_styles_display.short_description = 'Additional Styles'

    def selected_styles_display(self, obj):
        names = [
            assignment.style.qualified_name
            for assignment in self._active_style_assignments(obj)
        ]
        return ', '.join(names) if names else '—'
    selected_styles_display.short_description = 'Selected Styles'

    def available_styles_display(self, obj):
        styles = Style.objects.filter(
            geodata_engine=obj.workspace.geodata_engine,
        ).select_related('workspace').order_by('workspace__name', 'name')
        names = [style.qualified_name for style in styles]
        return ', '.join(names) if names else '—'
    available_styles_display.short_description = 'Available Styles'

    # ------------------------------------------------------------------
    # 4.2.4 — save_model: sync title/description to GeoServer for PUBLISHED
    # ------------------------------------------------------------------
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user

        handled_metadata = False
        if change and obj.publishing_state == 'PUBLISHED' and {'title', 'description'} & set(form.changed_data):
            try:
                LayerService.update_published_metadata(
                    layer=obj,
                    title=obj.title,
                    description=obj.description,
                )
                handled_metadata = True
            except Exception as exc:
                self.message_user(
                    request,
                    f'GeoServer update failed — save aborted: {exc}',
                    messages.ERROR,
                )
                return

        if not handled_metadata or set(form.changed_data) - {'title', 'description'}:
            with transaction.atomic():
                super().save_model(request, obj, form, change)
        _run_workspace_sync(self, request, obj.workspace)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, LayerStyleAssignment) and not obj.pk:
                obj.created_by = request.user
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()

    # ------------------------------------------------------------------
    # 4.5 — Delete safety: GeoServer-first for PUBLISHED layers
    # ------------------------------------------------------------------
    def delete_view(self, request, object_id, extra_context=None):
        """Inject GeoStory/Event/GeoFeedback usage counts into the page."""
        try:
            layer = self.get_object(request, object_id)
        except Exception:
            layer = None

        if layer is not None:
            usage = layer.usage_summary()
            if any(usage.values()):
                self.message_user(
                    request,
                    (
                        f"Layer '{layer.name}' is referenced by "
                        f"{usage['geostories']} geostories, "
                        f"{usage['events']} events, and "
                        f"{usage['feedbacks']} feedbacks. "
                        "Confirming will cascade-remove those references."
                    ),
                    messages.WARNING,
                )
            extra_context = {**(extra_context or {}), "layer_usage_summary": usage}

        return super().delete_view(request, object_id, extra_context=extra_context)

    def delete_model(self, request, obj):
        result = LayerService.delete_layer_safe(obj)
        if not result.get('success'):
            raise DeleteAborted(
                f"Cannot delete layer '{obj.name}': "
                f"{result.get('error', result.get('message', 'GeoServer deletion failed.'))}"
            )
        if result.get('already_deleted'):
            self.message_user(
                request,
                f"Layer '{obj.name}' was already absent in GeoServer. Django record was removed.",
                messages.WARNING,
            )

    def delete_queryset(self, request, queryset):
        for obj in queryset.select_related('workspace__geodata_engine', 'store'):
            result = LayerService.delete_layer_safe(obj)
            if not result.get('success'):
                self.message_user(
                    request,
                    f"Layer '{obj.name}' NOT deleted — "
                    f"{result.get('error', result.get('message', 'unknown'))}",
                    messages.ERROR,
                )
                continue
            if result.get('already_deleted'):
                self.message_user(
                    request,
                    f"Layer '{obj.name}' was already absent in GeoServer. Django record was removed.",
                    messages.WARNING,
                )


@admin.register(Style)
class StyleAdmin(admin.ModelAdmin):
    form = StyleAdminForm
    change_form_template = 'admin/geodata_providers/style/change_form.html'
    list_display = [
        'name', 'title', 'format', 'provider_link', 'workspace_link',
        'validation_state_badge', 'remote_state_badge', 'layer_link_count', 'updated_at',
    ]
    list_filter = ['format', 'validation_state', 'remote_state', 'geodata_engine', 'workspace']
    search_fields = ['name', 'title', 'description', 'file_name']
    readonly_fields = [
        'id', 'content_hash', 'validation_state_badge', 'validation_errors_display',
        'remote_state_badge', 'remote_error_display', 'remote_uploaded_at',
        'remote_verified_at', 'created_at', 'updated_at',
    ]
    list_per_page = 25

    fieldsets = (
        ('Identity', {
            'fields': ('geodata_engine', 'workspace', 'name', 'title', 'description'),
        }),
        ('Content', {
            'fields': ('format', 'upload_file', 'file_name', 'file_content', 'content_hash'),
        }),
        ('Validation Result', {
            'fields': ('validation_state_badge', 'validation_errors_display'),
        }),
        ('Remote Result', {
            'fields': ('remote_state_badge', 'remote_error_display', 'remote_uploaded_at', 'remote_verified_at'),
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'geodata_engine', 'workspace',
        ).annotate(_layer_link_count=Count('layer_assignments', distinct=True))

    def get_exclude(self, request, obj=None):
        return ['created_by']

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'workspace' in form.base_fields:
            form.base_fields['workspace'].queryset = (
                Workspace.objects.select_related('geodata_engine')
                .all().order_by('geodata_engine__name', 'name')
            )
        return form

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        if form.instance.validation_state:
            obj.validation_state = form.instance.validation_state
            obj.validation_errors = form.instance.validation_errors
        if obj.geodata_engine.engine_type == 'geoserver':
            obj.remote_state = 'LOCAL_ONLY'
            obj.remote_error = ''
        else:
            obj.remote_state = 'UNSUPPORTED'
            obj.remote_error = 'Remote style sync is not supported by this provider type.'
        super().save_model(request, obj, form, change)
        self._sync_remote_if_supported(request, obj)

    def provider_link(self, obj):
        return format_html('<a href="{}">{}</a>', _admin_change_url(obj.geodata_engine), obj.geodata_engine.name)
    provider_link.short_description = 'Provider'
    provider_link.admin_order_field = 'geodata_engine__name'

    def workspace_link(self, obj):
        if not obj.workspace:
            return 'Global'
        return format_html('<a href="{}">{}</a>', _admin_change_url(obj.workspace), obj.workspace.name)
    workspace_link.short_description = 'Workspace'
    workspace_link.admin_order_field = 'workspace__name'

    def layer_link_count(self, obj):
        return obj._layer_link_count
    layer_link_count.short_description = 'Layer Links'
    layer_link_count.admin_order_field = '_layer_link_count'

    def validation_state_badge(self, obj):
        colour = {'VALID': '#198754', 'INVALID': '#dc3545', 'UNKNOWN': '#6c757d'}.get(
            obj.validation_state, '#6c757d'
        )
        return format_html('<strong style="color:{};">{}</strong>', colour, obj.validation_state)
    validation_state_badge.short_description = 'Validation state'

    def validation_errors_display(self, obj):
        if not obj.validation_errors:
            return '—'
        return format_html('<pre style="white-space:pre-wrap;margin:0;">{}</pre>', json.dumps(obj.validation_errors, indent=2))
    validation_errors_display.short_description = 'Validation errors'

    def remote_state_badge(self, obj):
        colour = {
            'SYNCED': '#198754',
            'FAILED': '#dc3545',
            'UNSUPPORTED': '#6c757d',
            'LOCAL_ONLY': '#0d6efd',
            'DELETED': '#6c757d',
        }.get(obj.remote_state, '#6c757d')
        return format_html('<strong style="color:{};">{}</strong>', colour, obj.remote_state)
    remote_state_badge.short_description = 'Remote state'

    def remote_error_display(self, obj):
        return obj.remote_error or '—'
    remote_error_display.short_description = 'Remote error'

    def _sync_remote_if_supported(self, request, obj):
        if obj.geodata_engine.engine_type != 'geoserver':
            obj.remote_state = 'UNSUPPORTED'
            obj.remote_error = 'Remote style sync is not supported by this provider type.'
            obj.save(update_fields=['remote_state', 'remote_error', 'updated_at'])
            self.message_user(
                request,
                'Style validated and saved locally. Remote style sync is unsupported for this provider.',
                messages.INFO,
            )
            return

        client = EngineClientFactory.create_client(obj.geodata_engine)
        result = client.upload_style(
            name=obj.name,
            content=obj.file_content,
            style_format=obj.format,
            workspace=obj.workspace.name if obj.workspace else None,
            overwrite=True,
        )
        if result.get('success'):
            obj.remote_state = 'SYNCED'
            obj.remote_error = ''
            obj.remote_uploaded_at = timezone.now()
            obj.remote_verified_at = timezone.now()
            obj.save(update_fields=[
                'remote_state', 'remote_error', 'remote_uploaded_at',
                'remote_verified_at', 'updated_at',
            ])
            self.message_user(request, 'Style uploaded to GeoServer and verified.', messages.SUCCESS)
            return

        obj.remote_state = 'FAILED'
        obj.remote_error = result.get('error') or result.get('message', 'Remote sync failed.')
        obj.save(update_fields=['remote_state', 'remote_error', 'updated_at'])
        self.message_user(
            request,
            f"Style saved locally, but GeoServer sync failed: {obj.remote_error}",
            messages.ERROR,
        )

    def _ensure_local_style_content(self, obj):
        if obj.file_content or obj.geodata_engine.engine_type != 'geoserver':
            return
        client = EngineClientFactory.create_client(obj.geodata_engine)
        payload = client.get_style_content(
            name=obj.name,
            workspace=obj.workspace.name if obj.workspace else None,
        )
        if not payload:
            return
        obj.file_content = payload.get('content', '')
        obj.file_name = payload.get('file_name', obj.file_name)
        obj.format = payload.get('format', obj.format)
        if obj.file_content:
            validation = StyleValidationService.validate(
                content=obj.file_content,
                style_format=obj.format,
            )
            obj.validation_state = 'VALID' if validation.get('valid') else 'INVALID'
            obj.validation_errors = validation.get('errors', [])
        obj.save(update_fields=[
            'file_content',
            'file_name',
            'format',
            'validation_state',
            'validation_errors',
            'content_hash',
            'updated_at',
        ])

    def delete_model(self, request, obj):
        self._ensure_style_can_be_deleted(obj)
        result = self._delete_style_remote_first(obj)
        if not result.get('success'):
            raise DeleteAborted(
                f"Cannot delete style '{obj.name}': "
                f"{result.get('error', result.get('message', 'GeoServer deletion failed.'))}"
            )
        super().delete_model(request, obj)
        if result.get('already_deleted'):
            self.message_user(
                request,
                f"Style '{obj.name}' was already absent in GeoServer. Django record was removed.",
                messages.WARNING,
            )

    def delete_queryset(self, request, queryset):
        for obj in queryset.select_related('geodata_engine', 'workspace'):
            try:
                self._ensure_style_can_be_deleted(obj)
                result = self._delete_style_remote_first(obj)
                if not result.get('success'):
                    self.message_user(
                        request,
                        f"Style '{obj.name}' NOT deleted — "
                        f"{result.get('error', result.get('message', 'unknown'))}",
                        messages.ERROR,
                    )
                    continue
                obj.delete()
                if result.get('already_deleted'):
                    self.message_user(
                        request,
                        f"Style '{obj.name}' was already absent in GeoServer. Django record was removed.",
                        messages.WARNING,
                    )
            except DeleteAborted as exc:
                self.message_user(request, str(exc), messages.ERROR)

    def _ensure_style_can_be_deleted(self, obj):
        assignment_count = obj.layer_assignments.filter(is_active=True).count()
        if assignment_count:
            raise DeleteAborted(
                f"Cannot delete style '{obj.name}': active layer assignments exist ({assignment_count})."
            )

    def _delete_style_remote_first(self, obj):
        if obj.geodata_engine.engine_type != 'geoserver':
            return {
                'success': True,
                'message': 'Remote style sync unsupported; deleted from Django only.',
            }
        client = EngineClientFactory.create_client(obj.geodata_engine)
        return client.delete_style(
            name=obj.name,
            workspace=obj.workspace.name if obj.workspace else None,
        )

_patch_geodata_providers_admin_app_list()
