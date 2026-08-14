import json
import logging
import os

from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django import forms
from django.forms.models import BaseInlineFormSet, ModelChoiceIteratorValue
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.utils.html import format_html
from django.utils import timezone
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.core.exceptions import ValidationError
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
    workspace_sync_view, workspace_visibility_toggle_view,
    store_postgis_tables_view, store_clone_view,
    publish_postgis_view, stores_for_workspace_view, tables_for_store_view,
)
from tosca_api.apps.organizations.permissions import OrgScopedAdminMixin, resolve_write_organization

from .engine_factory import EngineClientFactory
from .exceptions import GeoServerConnectionError, GeodataEngineError
from .models import (
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
from .services.commands.geodata_engine_service import GeodataEngineService
from .services.commands.layer_group_service import LayerGroupService
from .services.commands.layer_service import LayerService
from .services.commands.store_service import StoreService
from .services.commands.style_validation_service import StyleValidationService
from .services.commands.workspace_service import WorkspaceService
from tosca_api.apps.core.editorjs import validate_description_document
from tosca_api.apps.geocontext.widgets import EditorJsWidget

logger = logging.getLogger(__name__)

_GEODATA_PROVIDER_ADMIN_ORDER = {
    'GeodataEngine': 0,
    'Workspace': 1,
    'Store': 2,
    'Layer': 3,
    'LayerGroup': 4,
    'Style': 5,
    'SpriteAsset': 6,
}

_GEODATA_PROVIDER_ADMIN_LABELS = {
    'GeodataEngine': 'Geodata Provider',
    'Workspace': 'Workspace',
    'Store': 'Store',
    'Layer': 'Layer',
    'LayerGroup': 'Layer Group',
    'Style': 'Style',
    'SpriteAsset': 'Sprite Asset',
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


def _active_workspace_queryset():
    return (
        Workspace.objects.select_related('geodata_engine')
        .filter(geodata_engine__is_active=True)
        .order_by('geodata_engine__name', 'name')
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
        results = service.sync_workspace_resources(workspace, created_by=request.user)
        store_result = results['stores']
        style_result = results['styles']
        layer_result = results['layers']
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


def _sync_state_badge(obj):
    colour = {
        'SYNCED': '#198754',
        'LOCAL_ONLY': '#0d6efd',
        'REMOTE_ONLY': '#6f42c1',
        'STALE': '#f5a623',
        'FAILED': '#dc3545',
        'UNKNOWN': '#6c757d',
    }.get(obj.sync_state, '#6c757d')
    return format_html('<strong style="color:{};">{}</strong>', colour, obj.sync_state)


# Admin Forms
class GeodataEngineForm(forms.ModelForm):
    class Meta:
        model = GeodataEngine
        fields = '__all__'
        widgets = {
            'admin_password': forms.PasswordInput(render_value=False),
            'api_key': forms.PasswordInput(render_value=False),
            'base_url': forms.TextInput(attrs={'size': 60}),
            'public_url': forms.TextInput(attrs={'size': 60}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # MARTIN/PG_TILESERV have no working client implementation — hide them
        # from selection, but keep a pre-existing row's own type selectable so
        # editing it doesn't fail on a field the user isn't even changing.
        current = getattr(self.instance, 'engine_type', None)
        allowed = {GeodataEngine.EngineType.GEOSERVER, current} if current else {GeodataEngine.EngineType.GEOSERVER}
        self.fields['engine_type'].choices = [
            choice for choice in GeodataEngine.EngineType.choices if choice[0] in allowed
        ]

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
            public_url=cleaned_data.get('public_url') or self.instance.public_url,
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
    is_public = forms.BooleanField(
        required=False,
        label='Public workspace',
        help_text='Let anyone read the layers. Only the owning organization can edit.',
    )

    class Meta:
        model = Workspace
        exclude = ['visibility']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['is_public'].initial = (
            self.instance.visibility == Workspace.Visibility.PUBLIC
        )
        if 'organization' in self.fields:
            self.fields['organization'].help_text = (
                'The organization that owns this workspace and its layers.'
            )

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

    def save(self, commit=True):
        self.instance.visibility = (
            Workspace.Visibility.PUBLIC
            if self.cleaned_data.get('is_public')
            else Workspace.Visibility.PRIVATE
        )
        return super().save(commit=commit)


# GeodataEngine Admin - Engine Management
@admin.register(GeodataEngine)
class GeodataEngineAdmin(RemoteDeleteAdminMixin, admin.ModelAdmin):
    form = GeodataEngineForm
    change_form_template = 'admin/geodata_providers/geodataengine/change_form.html'
    list_display = [
        'name', 'engine_type', 'base_url', 'public_url',
        'is_active', 'is_default',
        'connection_status_badge', 'workspace_count', 'style_count',
        'layer_count', 'active_layer_settings_count',
    ]
    list_filter = ['engine_type', 'is_active', 'is_default']
    search_fields = ['name', 'base_url', 'public_url']
    readonly_fields = ['id', 'created_at', 'updated_at']
    list_per_page = 25
    actions = [sync_engines, test_connection, set_as_default, deactivate_engines, reactivate_engines]

    fieldsets = (
        ('Identity', {
            'fields': ('name', 'description', 'engine_type'),
        }),
        ('Connection', {
            'fields': ('base_url', 'public_url', 'admin_username', 'admin_password', 'api_key'),
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
class WorkspaceAdmin(OrgScopedAdminMixin, RemoteDeleteAdminMixin, admin.ModelAdmin):
    form = WorkspaceAdminForm
    change_form_template = 'admin/geodata_providers/workspace/change_form.html'
    list_display = ['name', 'organization', 'engine_link', 'description', 'sync_state_badge', 'store_count', 'layer_count', 'created_at']
    list_filter = ['organization', 'geodata_engine', 'geodata_engine__engine_type', 'sync_state']
    search_fields = ['name', 'geodata_engine__name']
    readonly_fields = ['id', 'sync_state_badge', 'last_sync_at', 'last_sync_error', 'remote_identifier', 'remote_hash', 'created_at', 'updated_at']
    inlines = [StoreInline]
    actions = [sync_workspaces]
    list_per_page = 25

    fieldsets = (
        ('Identity', {
            'fields': ('geodata_engine', 'organization', 'is_public', 'name', 'description'),
        }),
        ('Metadata', {
            'fields': ('id', 'sync_state_badge', 'last_sync_at', 'last_sync_error', 'remote_identifier', 'remote_hash', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    # ------------------------------------------------------------------
    # Default the Organization field to the caller's own org on the add
    # form, so org-scoped staff (and superusers with a resolvable org) see
    # their organization pre-selected instead of an empty dropdown.
    # ------------------------------------------------------------------
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        organization = resolve_write_organization(request)
        if organization is not None:
            initial.setdefault('organization', organization.pk)
        return initial

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
            path(
                '<uuid:workspace_id>/toggle-visibility/',
                self.admin_site.admin_view(workspace_visibility_toggle_view),
                name='workspace_toggle_visibility',
            ),
        ]
        return custom + super().get_urls()

    # ------------------------------------------------------------------
    # Queryset — annotate store + layer counts (tasks 2.1.6)
    # ------------------------------------------------------------------
    def get_queryset(self, request):
        qs = (
            super().get_queryset(request)
            .select_related('geodata_engine')
            .filter(geodata_engine__is_active=True)
        )
        return qs.annotate(
            _store_count=Count('stores', distinct=True),
            _layer_count=Count('layers', distinct=True),
        )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if obj:  # lock engine when editing existing
            readonly += ['geodata_engine', 'name']
        if not request.user.is_superuser:
            # Org-scoped staff can only ever create/see rows in their own
            # org (see get_queryset); the field is derived, not chosen.
            readonly += ['organization']
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

    def sync_state_badge(self, obj):
        return _sync_state_badge(obj)
    sync_state_badge.short_description = 'Sync state'
    sync_state_badge.admin_order_field = 'sync_state'

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
            organization = form.cleaned_data.get('organization') or resolve_write_organization(request)
            if organization is None:
                raise ValidationError('Could not determine an organization for this workspace.')
            result = WorkspaceService.create_workspace(
                engine=form.cleaned_data.get('geodata_engine'),
                organization=organization,
                name=form.cleaned_data.get('name', obj.name),
                description=form.cleaned_data.get('description', obj.description),
                visibility=obj.visibility,
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
    connection_fields_changed = False

    class Meta:
        model = Store
        fields = '__all__'
        exclude = (
            'sync_state',
            'last_sync_at',
            'last_sync_error',
            'remote_identifier',
            'remote_hash',
        )
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

        if not self.instance._state.adding and engine and store_type == 'postgis':
            changed_fields = self._changed_connection_fields(cleaned_data)
            self.connection_fields_changed = bool(changed_fields)
            if changed_fields:
                try:
                    password = cleaned_data.get('password') or self.instance.decrypted_password
                except (ValueError, Exception):
                    password = ''
                result = StoreService.test_store_connection(
                    store_type=store_type,
                    host=cleaned_data.get('host') or '',
                    port=cleaned_data.get('port') or 5432,
                    database=cleaned_data.get('database') or '',
                    username=cleaned_data.get('username') or '',
                    password=password,
                    schema=cleaned_data.get('schema') or 'public',
                )
                if not result.get('success'):
                    field_errors = result.get('details', {}).get('field_errors', {})
                    for field, message in field_errors.items():
                        if field in self.fields:
                            self.add_error(field, message)
                    if not field_errors:
                        raise ValidationError(
                            result.get('error') or 'Store connection validation failed.'
                        )
        return cleaned_data

    def _changed_connection_fields(self, cleaned_data):
        if self.instance._state.adding:
            return []
        changed_fields = [
            field for field in ['host', 'port', 'database', 'username', 'schema']
            if cleaned_data.get(field) != getattr(self.instance, field)
        ]
        if cleaned_data.get('password'):
            changed_fields.append('password')
        return changed_fields

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

        result = StoreService.test_store_connection(
            store_type=store_type,
            host=self.cleaned_data.get('host') or '',
            port=self.cleaned_data.get('port') or 5432,
            database=self.cleaned_data.get('database') or '',
            username=self.cleaned_data.get('username') or '',
            password=password,
            schema=self.cleaned_data.get('schema') or 'public',
        )
        if result.get('success'):
            return

        field_errors = result.get('details', {}).get('field_errors', {})
        for field, message in field_errors.items():
            if field in self.fields:
                self.add_error(field, message)
        if not field_errors:
            self.add_error(None, result.get('error') or 'Store connection validation failed.')


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
            'format', 'upload_file', 'file_name', 'file_content', 'sprite_asset',
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


class LayerAdminForm(forms.ModelForm):
    """Layer metadata form with the constrained public-description editor."""

    class Meta:
        model = Layer
        fields = '__all__'
        widgets = {
            'description_content': EditorJsWidget(profile='description'),
        }

    def clean_description_content(self):
        try:
            return validate_description_document(self.cleaned_data.get('description_content'))
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)


class LayerStyleAssignmentAdminForm(forms.ModelForm):
    class Meta:
        model = LayerStyleAssignment
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Inline rows must be validated as one final state. A row-level query
        # sees the old database values and incorrectly rejects default swaps.
        self.instance._defer_active_default_validation = True

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


class LayerStyleAssignmentInlineFormSet(BaseInlineFormSet):
    """Validate the final inline state instead of each row in isolation."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        active_defaults = [
            form
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get('DELETE')
            and form.cleaned_data.get('role') == LayerStyleAssignment.Role.DEFAULT
            and form.cleaned_data.get('is_active')
        ]
        if len(active_defaults) > 1:
            raise ValidationError('Only one active default style is allowed per layer.')


class StoreCloneForm(forms.Form):
    """Form used by the clone-store admin view."""
    name = forms.CharField(
        max_length=100,
        label='New store name',
        help_text='Must be unique within the target workspace.',
    )
    workspace = forms.ModelChoiceField(
        queryset=_active_workspace_queryset(),
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
    clone_layers = forms.BooleanField(
        required=False,
        initial=False,
        label='Clone layers too',
        help_text='Leave off to clone only the store. Enable to copy/publish source layers and valid style assignments.',
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
        'host', 'schema', 'sync_state_badge', 'geoserver_access_badge', 'layer_count',
    ]
    list_filter = ['workspace__geodata_engine', 'workspace', 'store_type', 'sync_state', NoCredentialFilter]
    search_fields = ['name', 'workspace__name', 'host', 'schema']
    readonly_fields = ['provider_link', 'id', 'sync_state_badge', 'last_sync_at', 'last_sync_error', 'remote_identifier', 'remote_hash', 'created_at', 'updated_at']
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
            'fields': ('id', 'sync_state_badge', 'last_sync_at', 'last_sync_error', 'remote_identifier', 'remote_hash', 'created_at', 'updated_at'),
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
        qs = (
            super().get_queryset(request)
            .select_related('workspace__geodata_engine')
            .filter(workspace__geodata_engine__is_active=True)
        )
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
            form.base_fields['workspace'].queryset = _active_workspace_queryset()
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

    def sync_state_badge(self, obj):
        return _sync_state_badge(obj)
    sync_state_badge.short_description = 'Sync state'
    sync_state_badge.admin_order_field = 'sync_state'

    # ------------------------------------------------------------------
    # Save — preserve existing encrypted password if submitted blank (3.2.4)
    # ------------------------------------------------------------------
    def save_model(self, request, obj, form, change):
        connection_fields_changed = (
            change
            and obj.store_type == Store.StoreType.POSTGIS
            and bool(getattr(form, 'connection_fields_changed', False))
        )
        if change and not form.cleaned_data.get('password'):
            # Reload the stored encrypted value so we never overwrite with ''
            obj.password = Store.objects.filter(pk=obj.pk).values_list('password', flat=True).first() or ''
        if obj.workspace and not obj.geodata_engine:
            obj.geodata_engine = obj.workspace.geodata_engine
        if not change:
            obj.created_by = request.user
            result = StoreService.create_postgis_store(
                workspace=obj.workspace,
                name=obj.name,
                user=request.user,
                store_type=obj.store_type,
                description=obj.description,
                host=obj.host,
                port=obj.port,
                database=obj.database,
                username=obj.username,
                password=form.cleaned_data.get('password') or obj.password,
                schema=obj.schema,
                file_path=obj.file_path,
                charset=obj.charset,
            )
            if not result.get('success'):
                raise ValidationError(result.get('message', 'Store create failed.'))
            store = result['resource']
            obj.__dict__.update(store.__dict__)
        elif connection_fields_changed:
            password = form.cleaned_data.get('password') or obj.decrypted_password
            result = StoreService.update_postgis_store_connection(
                store=Store.objects.select_related('workspace__geodata_engine').get(pk=obj.pk),
                host=obj.host,
                port=obj.port,
                database=obj.database,
                username=obj.username,
                password=password,
                schema=obj.schema,
                description=obj.description,
            )
            if not result.get('success'):
                raise ValidationError(result.get('message', 'Store update failed.'))
            store = result['resource']
            obj.__dict__.update(store.__dict__)
        else:
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
    formset = LayerStyleAssignmentInlineFormSet
    fields = ('style', 'role', 'style_layer_ids', 'is_active', 'created_at')
    readonly_fields = ('created_at',)
    extra = 0
    show_change_link = True


@admin.register(Layer)
class LayerAdmin(RemoteDeleteAdminMixin, admin.ModelAdmin):
    form = LayerAdminForm
    actions = [publish_layer, unpublish_layer]
    change_form_template = 'admin/geodata_providers/layer/change_form.html'
    list_display = [
        'name', 'title', 'workspace_link', 'store_name',
        'geometry_type', 'srid', 'default_style_name', 'publishing_state_badge', 'sync_state_badge', 'is_public',
    ]
    list_filter = [
        'workspace__geodata_engine', 'workspace', 'store',
        'publishing_state', 'sync_state', 'geometry_type', 'is_public',
    ]
    search_fields = ['name', 'title', 'table_name', 'workspace__name']
    readonly_fields = [
        'id', 'name', 'provider_link', 'table_name', 'geometry_column', 'geometry_type',
        'workspace', 'store', 'created_at', 'updated_at', 'publishing_state',
        'sync_state_badge', 'last_sync_at', 'last_sync_error', 'remote_identifier',
        'remote_hash', 'published_url', 'publishing_error', 'default_style_display',
        'additional_styles_display', 'available_styles_display', 'selected_styles_display',
        'description', 'provider_description',
    ]
    inlines = [LayerStyleInline]
    list_per_page = 25

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'name', 'provider_link', 'title', 'description_content'),
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
        ('Sync Result', {
            'fields': ('sync_state_badge', 'last_sync_at', 'last_sync_error', 'remote_identifier', 'remote_hash'),
        }),
        ('Layer Settings', {
            'fields': (
                'queryable', 'opaque', 'default_style_display',
                'additional_styles_display', 'available_styles_display',
                'selected_styles_display',
            ),
        }),
        ('Metadata', {
            'fields': ('description', 'provider_description', 'created_at', 'updated_at'),
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

    def sync_state_badge(self, obj):
        return _sync_state_badge(obj)
    sync_state_badge.short_description = 'Sync state'
    sync_state_badge.admin_order_field = 'sync_state'

    # ------------------------------------------------------------------
    # Queryset
    # ------------------------------------------------------------------
    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related('workspace__geodata_engine', 'store')
            .filter(workspace__geodata_engine__is_active=True)
            .prefetch_related('style_assignments__style', 'workspace__styles')
        )

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
        if change and obj.publishing_state == 'PUBLISHED' and {'title', 'description_content'} & set(form.changed_data):
            try:
                LayerService.update_published_metadata(
                    layer=obj,
                    title=obj.title,
                    description=obj.description,
                    description_content=obj.description_content,
                )
                handled_metadata = True
            except Exception as exc:
                self.message_user(
                    request,
                    f'GeoServer update failed — save aborted: {exc}',
                    messages.ERROR,
                )
                return

        if not handled_metadata or set(form.changed_data) - {'title', 'description_content'}:
            with transaction.atomic():
                super().save_model(request, obj, form, change)
        # Do not pull-sync the workspace here. Django saves inline style
        # assignments after save_model(), and a pull at this point lets the
        # provider's generic SLD defaults overwrite the submitted MBStyle
        # state before the formset is persisted. Explicit sync actions remain
        # available when provider metadata needs to be refreshed.

    def save_formset(self, request, form, formset, change):
        # Pure Django ORM work (no remote calls) — safe and necessary to
        # wrap in one transaction so a failure partway through a batch of
        # style-assignment saves/deletes can't leave the formset half-applied.
        with transaction.atomic():
            instances = formset.save(commit=False)
            for obj in formset.deleted_objects:
                obj.delete()

            # Release the existing default before activating its replacement.
            # The partial database constraint is immediate, so saving in the
            # visual inline order can otherwise create a transient duplicate.
            non_defaults = []
            active_defaults = []
            for obj in instances:
                if isinstance(obj, LayerStyleAssignment) and obj._state.adding:
                    obj.created_by = request.user
                if (
                    isinstance(obj, LayerStyleAssignment)
                    and obj.role == LayerStyleAssignment.Role.DEFAULT
                    and obj.is_active
                ):
                    active_defaults.append(obj)
                else:
                    non_defaults.append(obj)

            for obj in [*non_defaults, *active_defaults]:
                obj.save()
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
        'validation_state_badge', 'remote_state_badge', 'sync_state_badge', 'layer_link_count', 'updated_at',
    ]
    list_filter = ['geodata_engine', 'workspace', 'format', 'validation_state', 'remote_state', 'sync_state']
    search_fields = ['name', 'title', 'description', 'file_name']
    readonly_fields = [
        'id', 'content_hash', 'validation_state_badge', 'validation_errors_display',
        'remote_state_badge', 'remote_error_display', 'remote_uploaded_at',
        'remote_verified_at', 'sync_state_badge', 'last_sync_at', 'last_sync_error',
        'remote_identifier', 'remote_hash', 'created_at', 'updated_at',
    ]
    list_per_page = 25

    fieldsets = (
        ('Identity', {
            'fields': ('geodata_engine', 'workspace', 'name', 'title', 'description'),
        }),
        ('Content', {
            'fields': ('format', 'upload_file', 'file_name', 'file_content', 'sprite_asset', 'content_hash'),
        }),
        ('Validation Result', {
            'fields': ('validation_state_badge', 'validation_errors_display'),
        }),
        ('Remote Result', {
            'fields': ('remote_state_badge', 'remote_error_display', 'remote_uploaded_at', 'remote_verified_at'),
        }),
        ('Sync Result', {
            'fields': ('sync_state_badge', 'last_sync_at', 'last_sync_error', 'remote_identifier', 'remote_hash'),
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related('geodata_engine', 'workspace')
            .filter(geodata_engine__is_active=True)
            .annotate(_layer_link_count=Count('layer_assignments', distinct=True))
        )

    def get_exclude(self, request, obj=None):
        return ['created_by']

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'workspace' in form.base_fields:
            form.base_fields['workspace'].queryset = _active_workspace_queryset()
        if 'geodata_engine' in form.base_fields:
            form.base_fields['geodata_engine'].queryset = (
                GeodataEngine.objects.filter(is_active=True).order_by('name')
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

    def sync_state_badge(self, obj):
        return _sync_state_badge(obj)
    sync_state_badge.short_description = 'Sync state'
    sync_state_badge.admin_order_field = 'sync_state'

    def remote_error_display(self, obj):
        return obj.remote_error or '—'
    remote_error_display.short_description = 'Remote error'

    def _sync_remote_if_supported(self, request, obj):
        if obj.geodata_engine.engine_type != 'geoserver':
            obj.remote_state = 'UNSUPPORTED'
            obj.remote_error = 'Remote style sync is not supported by this provider type.'
            obj.sync_state = 'UNKNOWN'
            obj.last_sync_error = obj.remote_error
            obj.save(update_fields=['remote_state', 'remote_error', 'sync_state', 'last_sync_error', 'updated_at'])
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
            now = timezone.now()
            obj.remote_state = 'SYNCED'
            obj.remote_error = ''
            obj.remote_uploaded_at = now
            obj.remote_verified_at = now
            obj.sync_state = 'SYNCED'
            obj.last_sync_at = now
            obj.last_sync_error = ''
            obj.remote_identifier = obj.qualified_name
            obj.save(update_fields=[
                'remote_state', 'remote_error', 'remote_uploaded_at',
                'remote_verified_at', 'sync_state', 'last_sync_at',
                'last_sync_error', 'remote_identifier', 'updated_at',
            ])
            self.message_user(request, 'Style uploaded to GeoServer and verified.', messages.SUCCESS)
            return

        obj.remote_state = 'FAILED'
        obj.remote_error = result.get('error') or result.get('message', 'Remote sync failed.')
        obj.sync_state = 'FAILED'
        obj.last_sync_at = timezone.now()
        obj.last_sync_error = obj.remote_error
        obj.save(update_fields=[
            'remote_state', 'remote_error', 'sync_state', 'last_sync_at',
            'last_sync_error', 'updated_at',
        ])
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
        pinned_group_count = LayerGroupMember.objects.filter(
            style_assignment__style=obj,
        ).count()
        if assignment_count or pinned_group_count:
            raise DeleteAborted(
                f"Cannot delete style '{obj.name}': active layer assignments exist "
                f"({assignment_count} layers, {pinned_group_count} group members)."
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


class SpriteAssetAdminForm(forms.ModelForm):
    index_file = forms.FileField(
        required=False,
        label='Sprite JSON file',
        help_text='Upload the .json index paired with the sprite PNG.',
    )
    index_content = forms.JSONField(
        required=False,
        label='Index content',
        help_text='Populated automatically from the uploaded sprite JSON file, or paste JSON here.',
        widget=forms.Textarea(attrs={'rows': 14, 'style': 'font-family:monospace;'}),
    )
    index_file_2x = forms.FileField(
        required=False,
        label='Sprite @2x JSON file',
        help_text='Upload the @2x .json index paired with the high-DPI sprite PNG.',
    )
    index_content_2x = forms.JSONField(
        required=False,
        label='@2x index content',
        help_text='Populated automatically from the uploaded @2x sprite JSON file.',
        widget=forms.Textarea(attrs={'rows': 14, 'style': 'font-family:monospace;'}),
    )

    class Meta:
        model = SpriteAsset
        fields = (
            'geodata_engine', 'workspace', 'name', 'image',
            'index_file', 'index_content',
            'image_2x', 'index_file_2x', 'index_content_2x',
        )

    def clean(self):
        cleaned_data = super().clean()
        index_file = cleaned_data.get('index_file')
        if index_file:
            try:
                cleaned_data['index_content'] = json.loads(index_file.read().decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.add_error('index_file', f'Invalid sprite JSON: {exc}')
        elif not cleaned_data.get('index_content'):
            self.add_error(
                'index_content',
                'Upload a sprite JSON file or paste its index content.',
            )

        index_file_2x = cleaned_data.get('index_file_2x')
        if index_file_2x:
            try:
                cleaned_data['index_content_2x'] = json.loads(
                    index_file_2x.read().decode('utf-8')
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.add_error('index_file_2x', f'Invalid @2x sprite JSON: {exc}')
        return cleaned_data


@admin.register(SpriteAsset)
class SpriteAssetAdmin(admin.ModelAdmin):
    form = SpriteAssetAdminForm
    list_display = ('name', 'geodata_engine', 'workspace', 'validation_state', 'updated_at')
    list_filter = ('geodata_engine', 'workspace', 'validation_state')
    search_fields = ('name',)
    readonly_fields = (
        'id', 'sprite_preview', 'content_hash', 'validation_state', 'validation_errors',
        'created_at', 'updated_at',
    )
    fieldsets = (
        ('Identity', {'fields': ('id', 'geodata_engine', 'workspace', 'name')}),
        ('Sprite 1x files', {'fields': ('image', 'index_file', 'index_content')}),
        ('Sprite @2x files', {
            'fields': ('image_2x', 'index_file_2x', 'index_content_2x'),
            'description': (
                'Optional high-DPI pair. MapLibre requests these files automatically on '
                'high-resolution displays.'
            ),
        }),
        ('Preview', {'fields': ('sprite_preview',)}),
        ('Validation', {'fields': ('validation_state', 'validation_errors', 'content_hash')}),
        ('Metadata', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    class Media:
        css = {'all': ('admin/css/sprite_asset_preview.css',)}
        js = ('admin/js/sprite_asset_preview.js',)

    @admin.display(description='Sprite preview')
    def sprite_preview(self, obj):
        image_url = obj.image.url if obj and obj.image else ''
        image_url_2x = obj.image_2x.url if obj and obj.image_2x else ''
        index = obj.index_content if obj else {}
        index_2x = obj.index_content_2x if obj else {}
        return format_html(
            '<div class="sprite-asset-preview" data-sprite-preview '
            'data-image-url="{}" data-image-url-2x="{}" '
            'data-index="{}" data-index-2x="{}">'
            '<p class="sprite-preview-empty">Upload a PNG and JSON index to preview '
            'the sheet and its individual images.</p>'
            '</div>',
            image_url,
            image_url_2x,
            json.dumps(index, separators=(',', ':')),
            json.dumps(index_2x, separators=(',', ':')),
        )

    def get_exclude(self, request, obj=None):
        return ['created_by']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class LayerStyleAssignmentSelect(forms.Select):
    """Expose assignment metadata to the inline without showing relation labels."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name,
            value,
            label,
            selected,
            index,
            subindex=subindex,
            attrs=attrs,
        )
        if isinstance(value, ModelChoiceIteratorValue):
            assignment = value.instance
            option['attrs']['data-layer-id'] = str(assignment.layer_id)
            option['attrs']['data-role'] = assignment.role
        return option


class LayerStyleAssignmentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, assignment):
        style = assignment.style
        label = style.title or style.name
        return label if assignment.is_active else f"{label} (inactive)"


class LayerGroupAdminForm(forms.ModelForm):
    confirm_legend_current = forms.BooleanField(
        required=False,
        label='The existing uploaded legend is still accurate',
        help_text=(
            'Select this when the current legend still represents the members, order, '
            'and styles. Saving will mark it as current without another upload.'
        ),
    )

    class Meta:
        model = LayerGroup
        fields = '__all__'
        widgets = {
            'description_content': EditorJsWidget(profile='description'),
        }

    def clean_description_content(self):
        try:
            return validate_description_document(self.cleaned_data.get('description_content'))
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)

    def clean_confirm_legend_current(self):
        confirmed = self.cleaned_data['confirm_legend_current']
        if confirmed and not self.cleaned_data.get('legend_image'):
            raise ValidationError('Upload or retain a group legend before confirming it.')
        return confirmed


class LayerGroupMemberInlineForm(forms.ModelForm):
    style_assignment = LayerStyleAssignmentChoiceField(
        queryset=LayerStyleAssignment.objects.none(),
        required=False,
        label='Style',
        empty_label="Select a layer to use its default style",
        widget=LayerStyleAssignmentSelect,
    )

    class Meta:
        model = LayerGroupMember
        exclude = ('source_alias',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assignment_field = self.fields.get('style_assignment')
        if assignment_field is not None:
            visible_assignments = Q(is_active=True)
            # Keep a pinned assignment visible even if a provider sync marked
            # it inactive. Otherwise the browser submits an empty value and the
            # form silently replaces it with the current default assignment.
            if self.instance.style_assignment_id:
                visible_assignments |= Q(pk=self.instance.style_assignment_id)
            assignment_field.queryset = (
                LayerStyleAssignment.objects.filter(visible_assignments)
                .select_related('style', 'layer')
                .order_by('layer__name', 'style__title', 'style__name')
            )
        if 'order' in self.fields:
            self.fields['order'].label = 'Order (0 = bottom)'
        if (
            self.instance.layer_id
            and self.instance.style_assignment_id is None
            and assignment_field is not None
        ):
            assignment_field.initial = self.instance.layer.style_assignments.filter(
                role=LayerStyleAssignment.Role.DEFAULT,
                is_active=True,
            ).first()

    def clean(self):
        cleaned_data = super().clean()
        layer = cleaned_data.get('layer')
        assignment = cleaned_data.get('style_assignment')
        if layer and assignment is None:
            assignment = layer.style_assignments.filter(
                role=LayerStyleAssignment.Role.DEFAULT,
                is_active=True,
            ).first()
            cleaned_data['style_assignment'] = assignment
            self.instance.style_assignment = assignment
        if layer and assignment and assignment.layer_id != layer.id:
            self.add_error(
                'style_assignment',
                'Select an assignment belonging to this layer.',
            )
        return cleaned_data


class LayerGroupMemberInlineFormSet(BaseInlineFormSet):
    """Assign derived values before Django evaluates uniqueness constraints."""

    def __init__(
        self,
        data=None,
        files=None,
        instance=None,
        save_as_new=False,
        prefix=None,
        queryset=None,
        **kwargs,
    ):
        resolved_prefix = prefix or self.get_default_prefix()
        if data is not None:
            data = data.copy()
            self._prepare_new_rows(data, resolved_prefix)
        super().__init__(
            data=data,
            files=files,
            instance=instance,
            save_as_new=save_as_new,
            prefix=prefix,
            queryset=queryset,
            **kwargs,
        )
        if not self.is_bound:
            current_max = (
                None
                if self.instance.pk is None
                else self.instance.members.aggregate(Max('order'))['order__max']
            )
            next_order = 0 if current_max is None else current_max + 1
            for offset, form in enumerate(self.extra_forms):
                form.initial['order'] = next_order + offset

    @staticmethod
    def _prepare_new_rows(data, prefix):
        try:
            total_forms = int(data.get(f'{prefix}-TOTAL_FORMS', 0))
            initial_forms = int(data.get(f'{prefix}-INITIAL_FORMS', 0))
        except (TypeError, ValueError):
            return

        retained_orders = []
        for index in range(initial_forms):
            if data.get(f'{prefix}-{index}-DELETE'):
                continue
            try:
                retained_orders.append(int(data.get(f'{prefix}-{index}-order', 0)))
            except (TypeError, ValueError):
                continue
        next_order = max(retained_orders, default=-1) + 1

        for index in range(initial_forms, total_forms):
            if data.get(f'{prefix}-{index}-DELETE'):
                continue
            layer_id = data.get(f'{prefix}-{index}-layer')
            if not layer_id:
                # A visible order hint must not make an otherwise empty extra form
                # count as changed and trigger a required-layer validation error.
                data[f'{prefix}-{index}-order'] = '0'
                continue
            data[f'{prefix}-{index}-order'] = str(next_order)
            next_order += 1
            assignment_key = f'{prefix}-{index}-style_assignment'
            if data.get(assignment_key):
                continue
            default_assignment = LayerStyleAssignment.objects.filter(
                layer_id=layer_id,
                role=LayerStyleAssignment.Role.DEFAULT,
                is_active=True,
            ).first()
            if default_assignment is not None:
                data[assignment_key] = str(default_assignment.id)

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        used_orders = set()
        for form in self.forms:
            if self._should_delete_form(form) or not form.cleaned_data.get('layer'):
                continue
            order = form.cleaned_data.get('order')
            if order in used_orders:
                raise ValidationError(
                    'Each member must have a unique order. Change an order to move that '
                    'member; the remaining members will be renumbered automatically.'
                )
            used_orders.add(order)

    def get_unique_error_message(self, unique_check):
        if 'order' in unique_check:
            return (
                'Each member must have a unique order. Change an order to move that '
                'member; the remaining members will be renumbered automatically.'
            )
        return super().get_unique_error_message(unique_check)

    def save(self, commit=True):
        """Save final member orders without transient uniqueness collisions."""
        if not commit or not self.instance.pk or not self._has_existing_order_changes():
            return super().save(commit=commit)

        with transaction.atomic():
            member_ids = list(
                LayerGroupMember.objects.select_for_update()
                .filter(group=self.instance)
                .values_list('pk', flat=True)
            )
            if member_ids:
                current_max = (
                    LayerGroupMember.objects.filter(pk__in=member_ids)
                    .aggregate(Max('order'))['order__max']
                    or 0
                )
                temporary_offset = current_max + len(member_ids) + len(self.forms) + 1
                LayerGroupMember.objects.filter(pk__in=member_ids).update(
                    order=F('order') + temporary_offset
                )

            saved_instances = super().save(commit=True)

            # Django skips unchanged forms. Restore those members from their
            # temporary order after all changed members have claimed final orders.
            for form in self.initial_forms:
                if self._should_delete_form(form) or form.has_changed():
                    continue
                form.instance.save(update_fields=['order', 'updated_at'])
            return saved_instances

    def _has_existing_order_changes(self):
        return any(
            not self._should_delete_form(form)
            and 'order' in form.changed_data
            for form in self.initial_forms
        )


class LayerGroupMemberInline(admin.TabularInline):
    model = LayerGroupMember
    form = LayerGroupMemberInlineForm
    formset = LayerGroupMemberInlineFormSet
    fields = (
        'title', 'layer', 'style_assignment', 'render_layer_ids',
        'order', 'source_alias_display',
    )
    readonly_fields = ('source_alias_display',)
    extra = 2
    autocomplete_fields = ('layer',)

    @admin.display(description='Source key (automatic)')
    def source_alias_display(self, obj):
        if obj is None or obj.pk is None or not obj.source_alias:
            return format_html(
                '<span>Generated when saved</span><br>'
                '<small style="color:var(--body-quiet-color);">'
                'Member key derived from the layer name; repeated vector data shares one source.</small>'
            )
        return format_html(
            '<code>{}</code><br><small style="color:var(--body-quiet-color);">'
            'Member key; repeated vector data shares one runtime source automatically.</small>',
            obj.source_alias,
        )

    class Media:
        js = ('admin/js/layer_group_members.js',)


@admin.register(LayerGroup)
class LayerGroupAdmin(admin.ModelAdmin):
    form = LayerGroupAdminForm
    list_display = (
        'name', 'title', 'workspace', 'composition_display', 'member_count',
        'publishing_state', 'is_public', 'updated_at',
    )
    list_filter = (
        'workspace__geodata_engine', 'workspace', 'publishing_state', 'is_public',
    )
    search_fields = ('name', 'title', 'description', 'workspace__name')
    readonly_fields = (
        'id', 'composition_display', 'publication_warnings_display',
        'legend_preview', 'legend_status_display', 'legend_content_hash',
        'legend_composition_hash',
        'publishing_error', 'sync_state', 'last_sync_at',
        'last_sync_error', 'description', 'created_at', 'updated_at',
    )
    inlines = (LayerGroupMemberInline,)
    fieldsets = (
        ('Identity', {'fields': ('id', 'workspace', 'name', 'title', 'description_content')}),
        ('Composition', {
            'fields': ('composition_display', 'publication_warnings_display'),
            'description': (
                'Members render from bottom to top: order 0 is the bottom and the highest '
                'order is the top. New orders, source keys, and default styles are assigned '
                'automatically.'
            ),
        }),
        ('Group legend', {
            'fields': (
                'legend_image', 'legend_preview', 'legend_status_display',
                'confirm_legend_current',
                'legend_content_hash', 'legend_composition_hash',
            ),
            'description': (
                'Upload one curated legend for the complete group. It replaces generated '
                'member legends in the map UI. Upload a new image after changing members, '
                'order, or pinned styles, or confirm that the existing legend is still accurate.'
            ),
        }),
        ('Publication', {'fields': ('publishing_state', 'is_public', 'publishing_error')}),
        ('Diagnostics', {
            'fields': ('sync_state', 'last_sync_at', 'last_sync_error'),
            'classes': ('collapse',),
        }),
        ('Metadata', {
            'fields': ('description', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_urls(self):
        custom = [
            path(
                'composition-warnings/',
                self.admin_site.admin_view(self.composition_warnings_view),
                name='geodata_providers_layergroup_composition_warnings',
            ),
        ]
        return custom + super().get_urls()

    def composition_warnings_view(self, request):
        """Evaluate the submitted member order before the admin saves it."""
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required.'}, status=405)
        if not (
            self.has_change_permission(request)
            or self.has_add_permission(request)
        ):
            return JsonResponse({'error': 'Permission denied.'}, status=403)
        try:
            payload = json.loads(request.body)
            submitted_members = payload['members']
            if not isinstance(submitted_members, list) or len(submitted_members) > 1000:
                raise ValueError
            layer_ids = [member['layer_id'] for member in submitted_members]
            layers = {
                str(layer_id): layer
                for layer_id, layer in Layer.objects.select_related('store').in_bulk(
                    layer_ids
                ).items()
            }
            layer_orders = [
                (layers[member['layer_id']], int(member['order']))
                for member in submitted_members
                if member['layer_id'] in layers
            ]
            group_id = payload.get('group_id')
            group = None if not group_id else LayerGroup.objects.filter(pk=group_id).first()
        except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid member data.'}, status=400)
        warnings = LayerGroup.publication_warnings_for_layers(layer_orders)
        legend_will_refresh = bool(payload.get('legend_will_refresh'))
        legend_will_be_removed = bool(payload.get('legend_will_be_removed'))
        legend_is_confirmed = bool(payload.get('legend_is_confirmed'))
        if (
            group is not None
            and group.legend_image
            and not legend_will_refresh
            and not legend_will_be_removed
            and not legend_is_confirmed
        ):
            current_members = [
                {
                    'id': str(member.id),
                    'layer_id': str(member.layer_id),
                    'style_assignment_id': str(member.style_assignment_id or ''),
                    'title': member.title,
                    'render_layer_ids': member.render_layer_ids,
                    'order': member.order,
                }
                for member in group.members.order_by('order', 'id')
            ]
            normalized_submitted = sorted(
                [
                    {
                        'id': str(member.get('id') or ''),
                        'layer_id': str(member['layer_id']),
                        'style_assignment_id': str(member.get('style_assignment_id') or ''),
                        'title': str(member.get('title') or ''),
                        'render_layer_ids': member.get('render_layer_ids') or [],
                        'order': int(member['order']),
                    }
                    for member in submitted_members
                ],
                key=lambda member: (member['order'], member['id']),
            )
            if group.legend_is_stale or normalized_submitted != current_members:
                warnings.append(
                    'The uploaded group legend will be outdated after these member, order, '
                    'or style changes. Upload a new legend or confirm that the existing '
                    'legend is still accurate.'
                )
        return JsonResponse({'warnings': warnings})

    def get_exclude(self, request, obj=None):
        return ['created_by']

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related('workspace__geodata_engine')
            .prefetch_related('members__layer__store')
        )

    def member_count(self, obj):
        return len(obj.members.all())
    member_count.short_description = 'Members'

    def composition_display(self, obj):
        if obj is None or obj.pk is None:
            return 'Determined by members'
        return obj.composition.title()
    composition_display.short_description = 'Composition'

    def publication_warnings_display(self, obj):
        if obj is None or obj.pk is None:
            return '—'
        warnings = obj.publication_warnings()
        return '—' if not warnings else ' | '.join(warnings)
    publication_warnings_display.short_description = 'Publication warnings'

    @admin.display(description='Legend preview')
    def legend_preview(self, obj):
        if obj is None or obj.pk is None or not obj.legend_image:
            return '—'
        return format_html(
            '<img src="{}" alt="Group legend preview" '
            'style="max-width:100%;max-height:240px;object-fit:contain;">',
            obj.legend_image.url,
        )

    @admin.display(description='Legend status')
    def legend_status_display(self, obj):
        if obj is None or obj.pk is None or not obj.legend_image:
            return 'No group legend uploaded'
        if obj.legend_is_stale:
            return format_html(
                '<strong style="color:var(--error-fg);">{}</strong>',
                'Outdated — upload a new legend or confirm that the existing legend is still accurate.',
            )
        return format_html(
            '<strong style="color:#2e7d32;">{}</strong>',
            'Current',
        )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        group = form.instance
        group._prefetched_objects_cache = {}
        refresh_legend = (
            'legend_image' in form.changed_data
            or form.cleaned_data.get('confirm_legend_current')
        )
        result = LayerGroupService.reconcile_publication(
            group=group,
            refresh_legend=refresh_legend,
        )
        if not result.ok:
            self.message_user(
                request,
                f'Layer group saved but not published: {result.error}',
                messages.ERROR,
            )
            return
        if group.legend_is_stale:
            self.message_user(
                request,
                'The uploaded group legend is outdated. Upload a new legend or confirm '
                'that the existing legend still matches the current group.',
                messages.WARNING,
            )

_patch_geodata_providers_admin_app_list()
