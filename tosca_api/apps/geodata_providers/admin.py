import logging

from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django import forms
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.utils.html import format_html
from django.contrib import messages
from django.db.models import Count
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import path, reverse
from .admin_actions import sync_engines, test_connection, set_as_default, sync_workspaces, clone_store, publish_layer, unpublish_layer
from .admin_views import (
    engine_test_connection_view, engine_sync_view,
    workspace_sync_view,
    store_postgis_tables_view, store_clone_view,
    publish_postgis_view, stores_for_workspace_view, tables_for_store_view,
)
from .engine_factory import EngineClientFactory
from .exceptions import GeoServerConnectionError, GeodataEngineError
from .models import GeodataEngine, Workspace, Store, Layer

logger = logging.getLogger(__name__)


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
        layer_result = service.sync_layers_for_workspace(workspace, created_by=request.user)
    except (GeoServerConnectionError, GeodataEngineError) as exc:
        modeladmin.message_user(
            request,
            f"Workspace '{workspace.name}' sync failed: {exc}",
            messages.WARNING,
        )
        return

    if store_result.get('errors') or layer_result.get('errors'):
        errors = store_result.get('errors', []) + layer_result.get('errors', [])
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
        if name.lower() == 'vector':
            raise ValidationError("Workspace name 'vector' is reserved.")
        return name

    def _post_clean(self):
        super()._post_clean()
        if self.errors or not self.instance._state.adding:
            return

        engine = self.cleaned_data.get('geodata_engine')
        name = self.cleaned_data.get('name')
        if not engine or not name:
            return

        try:
            client = EngineClientFactory.create_client(engine)
            result = client.create_workspace(name)
        except (GeoServerConnectionError, GeodataEngineError) as exc:
            self.add_error(None, f"Workspace could not be created in engine: {exc}")
            return

        if not result.get('success'):
            self.add_error(
                None,
                result.get('error') or result.get('message') or 'Workspace create failed in engine.',
            )
            return

        verification = client.post_verify_workspace(name, expected_exists=True)
        if not verification.get('verified'):
            self.add_error(
                None,
                verification.get('message') or 'Workspace creation could not be verified in engine.',
            )


# GeodataEngine Admin - Engine Management
@admin.register(GeodataEngine)
class GeodataEngineAdmin(RemoteDeleteAdminMixin, admin.ModelAdmin):
    form = GeodataEngineForm
    list_display = [
        'name', 'engine_type', 'base_url',
        'is_active', 'is_default',
        'connection_status_badge', 'workspace_count', 'layer_count',
    ]
    list_filter = ['engine_type', 'is_active', 'is_default']
    search_fields = ['name', 'base_url']
    readonly_fields = ['id', 'created_at', 'updated_at']
    list_per_page = 25
    actions = [sync_engines, test_connection, set_as_default]

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
        ]
        return custom + super().get_urls()

    # ------------------------------------------------------------------
    # Queryset — annotate counts so list columns are DB-efficient
    # ------------------------------------------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _workspace_count=Count('workspaces', distinct=True),
            _layer_count=Count('workspaces__layers', distinct=True),
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
        if change and not form.cleaned_data.get('admin_password'):
            obj.admin_password = (
                GeodataEngine.objects.filter(pk=obj.pk).values_list('admin_password', flat=True).first() or ''
            )
        if change and not form.cleaned_data.get('api_key'):
            obj.api_key = GeodataEngine.objects.filter(pk=obj.pk).values_list('api_key', flat=True).first() or ''
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        _run_engine_sync(self, request, obj)

    def get_exclude(self, request, obj=None):
        """Hide auto-managed fields from form."""
        return ['created_by']

    def delete_model(self, request, obj):
        dependency_counts = {
            'workspaces': obj.workspaces.count(),
            'stores': Store.objects.filter(workspace__geodata_engine=obj).count(),
            'layers': Layer.objects.filter(workspace__geodata_engine=obj).count(),
        }
        if any(dependency_counts.values()):
            details = ", ".join(
                f"{label}={value}" for label, value in dependency_counts.items() if value
            )
            raise DeleteAborted(
                f"Cannot delete engine '{obj.name}': dependent records exist ({details})."
            )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            dependency_counts = {
                'workspaces': obj.workspaces.count(),
                'stores': Store.objects.filter(workspace__geodata_engine=obj).count(),
                'layers': Layer.objects.filter(workspace__geodata_engine=obj).count(),
            }
            if any(dependency_counts.values()):
                details = ", ".join(
                    f"{label}={value}" for label, value in dependency_counts.items() if value
                )
                self.message_user(
                    request,
                    f"Engine '{obj.name}' NOT deleted: dependent records exist ({details}).",
                    messages.ERROR,
                )
                continue
            super().delete_model(request, obj)
    
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
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        exists_after_save = Workspace.objects.filter(pk=obj.pk).exists()
        logger.info(
            "WorkspaceAdmin.save_model persisted: pk=%s exists_after_save=%s",
            obj.pk,
            exists_after_save,
        )
        _run_workspace_sync(self, request, obj)

    def _workspace_dependency_counts(self, obj):
        return {
            'stores': obj.stores.count(),
            'layers': obj.layers.count(),
        }

    def _ensure_workspace_can_be_deleted(self, obj):
        counts = self._workspace_dependency_counts(obj)
        if any(counts.values()):
            details = ", ".join(
                f"{label}={value}" for label, value in counts.items() if value
            )
            raise DeleteAborted(
                f"Cannot delete workspace '{obj.name}': dependent records exist ({details})."
            )

    # ------------------------------------------------------------------
    # Delete safety (task 2.4) — engine-first, never delete Django if GeoServer fails
    # ------------------------------------------------------------------
    def delete_model(self, request, obj):
        # 2.4.4 — protect the 'vector' default workspace
        if obj.name == 'vector':
            self.message_user(
                request,
                "Default workspace 'vector' cannot be deleted.",
                messages.ERROR,
            )
            return

        self._ensure_workspace_can_be_deleted(obj)

        engine = obj.geodata_engine
        if not engine:
            obj.delete()
            return

        from .sync_service import GeoServerSyncService
        service = GeoServerSyncService(engine)
        result = service.delete_workspace_safe(obj)
        if not result.get('success'):
            raise DeleteAborted(
                f"Cannot delete workspace '{obj.name}': "
                f"{result.get('error', 'Engine deletion failed.')}"
            )
        if result.get('deleted') == 'engine_already_absent':
            self.message_user(
                request,
                f"Workspace '{obj.name}' was already absent in GeoServer. Django record was removed.",
                messages.WARNING,
            )

    def delete_queryset(self, request, queryset):
        from .sync_service import GeoServerSyncService

        for obj in queryset.select_related('geodata_engine'):
            # 2.4.4 — protect 'vector'
            if obj.name == 'vector':
                self.message_user(
                    request,
                    "Default workspace 'vector' cannot be deleted. Skipped.",
                    messages.ERROR,
                )
                continue

            try:
                self._ensure_workspace_can_be_deleted(obj)
            except DeleteAborted as exc:
                self.message_user(request, str(exc), messages.ERROR)
                continue

            engine = obj.geodata_engine
            if not engine:
                obj.delete()
                continue

            service = GeoServerSyncService(engine)
            result = service.delete_workspace_safe(obj)
            if not result.get('success'):
                self.message_user(
                    request,
                    f"Workspace '{obj.name}' NOT deleted — GeoServer error: "
                    f"{result.get('error', 'unknown')}",
                    messages.ERROR,
                )
            elif result.get('deleted') == 'engine_already_absent':
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
        'name', 'workspace_link', 'store_type',
        'host', 'schema', 'geoserver_access_badge', 'layer_count',
    ]
    list_filter = ['store_type', 'workspace__geodata_engine', 'workspace', NoCredentialFilter]
    search_fields = ['name', 'workspace__name', 'host', 'schema']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [LayerInline]
    list_per_page = 25
    change_form_template = 'admin/geodata_providers/store/change_form.html'

    fieldsets = (
        ('Identity', {
            'fields': ('name', 'workspace', 'store_type', 'description'),
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

    # ------------------------------------------------------------------
    # Computed list_display columns (tasks 3.1.2, 3.1.5)
    # ------------------------------------------------------------------
    def workspace_link(self, obj):
        if not obj.workspace:
            return '—'
        return format_html('<a href="{}">{}</a>', _admin_change_url(obj.workspace), obj.workspace.name)
    workspace_link.short_description = 'Workspace'
    workspace_link.admin_order_field = 'workspace__name'

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
        self._ensure_store_can_be_deleted(obj)
        engine = obj.workspace.geodata_engine if obj.workspace else None
        workspace_name = obj.workspace.name if obj.workspace else None

        if not engine or not workspace_name:
            obj.delete()
            return

        client = EngineClientFactory.create_client(engine)
        result = client.delete_store(workspace_name, obj.name)
        if not result.get('success'):
            raise DeleteAborted(
                f"Cannot delete store '{obj.name}': "
                f"{result.get('error', 'Engine deletion failed.')}"
            )
        if result.get('already_deleted'):
            self.message_user(
                request,
                f"Store '{obj.name}' was already absent in GeoServer. Django record was removed.",
                messages.WARNING,
            )
        obj.delete()

    def delete_queryset(self, request, queryset):
        for obj in queryset.select_related('workspace__geodata_engine'):
            try:
                self._ensure_store_can_be_deleted(obj)
            except DeleteAborted as exc:
                self.message_user(request, str(exc), messages.ERROR)
                continue

            engine = obj.workspace.geodata_engine if obj.workspace else None
            workspace_name = obj.workspace.name if obj.workspace else None

            if not engine or not workspace_name:
                obj.delete()
                continue

            client = EngineClientFactory.create_client(engine)
            result = client.delete_store(workspace_name, obj.name)
            if not result.get('success'):
                self.message_user(
                    request,
                    f"Store '{obj.name}' NOT deleted — GeoServer error: "
                    f"{result.get('error', 'unknown')}",
                    messages.ERROR,
                )
            else:
                if result.get('already_deleted'):
                    self.message_user(
                        request,
                        f"Store '{obj.name}' was already absent in GeoServer. Django record was removed.",
                        messages.WARNING,
                    )
                obj.delete()


# ======================================================================
# PHASE 4 — Layer Admin
# ======================================================================

@admin.register(Layer)
class LayerAdmin(RemoteDeleteAdminMixin, admin.ModelAdmin):
    actions = [publish_layer, unpublish_layer]
    list_display = [
        'name', 'title', 'workspace_link', 'store_name',
        'geometry_type', 'srid', 'publishing_state_badge', 'is_public',
    ]
    list_filter = ['publishing_state', 'geometry_type', 'workspace__geodata_engine', 'workspace', 'store', 'is_public']
    search_fields = ['name', 'title', 'table_name', 'workspace__name']
    readonly_fields = [
        'id', 'name', 'table_name', 'geometry_column', 'geometry_type',
        'workspace', 'store', 'created_at', 'updated_at', 'publishing_state',
        'published_url', 'publishing_error',
    ]
    list_per_page = 25

    fieldsets = (
        ('Identity', {
            'fields': ('id', 'name', 'title', 'description'),
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
        )

    # ------------------------------------------------------------------
    # 4.2.4 — save_model: sync title/description to GeoServer for PUBLISHED
    # ------------------------------------------------------------------
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user

        if change and obj.publishing_state == 'PUBLISHED':
            engine = obj.workspace.geodata_engine if obj.workspace else None
            if engine:
                try:
                    client = EngineClientFactory.create_client(engine)
                    client.update_featuretype(
                        workspace=obj.workspace.name,
                        store_name=obj.store.name,
                        featuretype_name=obj.name,
                        title=obj.title or obj.name,
                        abstract=obj.description or None,
                    )
                except Exception as exc:
                    self.message_user(
                        request,
                        f'GeoServer update failed — save aborted: {exc}',
                        messages.ERROR,
                    )
                    return   # abort Django save too

        super().save_model(request, obj, form, change)
        _run_workspace_sync(self, request, obj.workspace)

    # ------------------------------------------------------------------
    # 4.5 — Delete safety: GeoServer-first for PUBLISHED layers
    # ------------------------------------------------------------------
    def delete_model(self, request, obj):
        if obj.publishing_state == 'PUBLISHED':
            engine = obj.workspace.geodata_engine if obj.workspace else None
            if engine:
                client = EngineClientFactory.create_client(engine)
                result = client.delete_layer(obj.workspace.name, obj.name)
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
                # Verify gone
                still_there = client.verify_featuretype(
                    workspace=obj.workspace.name,
                    store_name=obj.store.name,
                    featuretype_name=obj.name,
                )
                if still_there:
                    raise DeleteAborted(
                        f"Cannot delete layer '{obj.name}': still exists in GeoServer after delete call."
                    )
        obj.delete()

    def delete_queryset(self, request, queryset):
        for obj in queryset.select_related('workspace__geodata_engine', 'store'):
            if obj.publishing_state == 'PUBLISHED':
                engine = obj.workspace.geodata_engine if obj.workspace else None
                if engine:
                    client = EngineClientFactory.create_client(engine)
                    result = client.delete_layer(obj.workspace.name, obj.name)
                    if not result.get('success'):
                        self.message_user(
                            request,
                            f"Layer '{obj.name}' NOT deleted — GeoServer error: "
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
                    still_there = client.verify_featuretype(
                        workspace=obj.workspace.name,
                        store_name=obj.store.name,
                        featuretype_name=obj.name,
                    )
                    if still_there:
                        self.message_user(
                            request,
                            f"Layer '{obj.name}' NOT deleted — still exists in GeoServer after delete call.",
                            messages.ERROR,
                        )
                        continue
            obj.delete()
