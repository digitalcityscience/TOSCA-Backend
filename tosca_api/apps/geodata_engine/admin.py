from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django import forms
from django.utils.html import format_html
from django.contrib import messages
from django.db.models import Count
from django.urls import path
from .admin_actions import sync_engines, test_connection, set_as_default, sync_workspaces, clone_store, publish_layer, unpublish_layer
from .admin_views import (
    engine_test_connection_view, engine_sync_view,
    workspace_sync_view,
    store_postgis_tables_view, store_clone_view,
    publish_postgis_view, stores_for_workspace_view,
)
from .engine_factory import EngineClientFactory
from .models import GeodataEngine, Workspace, Store, Layer


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
    

# GeodataEngine Admin - Engine Management
@admin.register(GeodataEngine)
class GeodataEngineAdmin(admin.ModelAdmin):
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
    change_form_template = 'admin/geodata_engine/geodataengine/change_form.html'

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
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_exclude(self, request, obj=None):
        """Hide auto-managed fields from form."""
        return ['created_by']
    
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
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'engine_link', 'description', 'store_count', 'layer_count', 'created_at']
    list_filter = ['geodata_engine', 'geodata_engine__engine_type']
    search_fields = ['name', 'geodata_engine__name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [StoreInline]
    actions = [sync_workspaces]
    list_per_page = 25
    change_form_template = 'admin/geodata_engine/workspace/change_form.html'

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
            readonly.append('geodata_engine')
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
        url = f'/admin/geodata_engine/geodataengine/{obj.geodata_engine.pk}/change/'
        return format_html('<a href="{}">{}</a>', url, obj.geodata_engine.name)
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
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

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

        engine = obj.geodata_engine
        if not engine:
            obj.delete()
            return

        from .sync_service import GeoServerSyncService
        from django.core.exceptions import PermissionDenied
        service = GeoServerSyncService(engine)
        result = service.delete_workspace_safe(obj)
        if not result.get('success'):
            raise PermissionDenied(
                f"Cannot delete workspace '{obj.name}': "
                f"{result.get('error', 'Engine deletion failed.')}"
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
class StoreAdmin(admin.ModelAdmin):
    form = StoreAdminForm
    actions = [clone_store]
    list_display = [
        'name', 'workspace_link', 'store_type',
        'host', 'schema', 'has_password_badge', 'layer_count',
    ]
    list_filter = ['store_type', 'workspace__geodata_engine', 'workspace', NoCredentialFilter]
    search_fields = ['name', 'workspace__name', 'host', 'schema']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [LayerInline]
    list_per_page = 25
    change_form_template = 'admin/geodata_engine/store/change_form.html'

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
        url = f'/admin/geodata_engine/workspace/{obj.workspace.pk}/change/'
        return format_html('<a href="{}">{}</a>', url, obj.workspace.name)
    workspace_link.short_description = 'Workspace'
    workspace_link.admin_order_field = 'workspace__name'

    def has_password_badge(self, obj):
        # Use decrypted_password — NOT bool(obj.password) — so a corrupt
        # encrypted token (decrypt raises ValueError) shows ✗ Missing, not ✓ Set.
        try:
            usable = bool(obj.decrypted_password)
        except (ValueError, Exception):
            usable = False
        if usable:
            return format_html('<span style="color:#3ecf8e;font-weight:600;">&#10003; Set</span>')
        return format_html('<span style="color:#e54d4d;font-weight:600;">&#10007; Missing</span>')
    has_password_badge.short_description = 'Credential'
    has_password_badge.admin_order_field = 'password'

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
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    # ------------------------------------------------------------------
    # Delete safety (task 3.5) — engine-first
    # ------------------------------------------------------------------
    def delete_model(self, request, obj):
        from django.core.exceptions import PermissionDenied

        engine = obj.workspace.geodata_engine if obj.workspace else None
        workspace_name = obj.workspace.name if obj.workspace else None

        if not engine or not workspace_name:
            obj.delete()
            return

        client = EngineClientFactory.create_client(engine)
        result = client.delete_store(workspace_name, obj.name)
        if not result.get('success'):
            raise PermissionDenied(
                f"Cannot delete store '{obj.name}': "
                f"{result.get('error', 'Engine deletion failed.')}"
            )
        obj.delete()

    def delete_queryset(self, request, queryset):
        from django.core.exceptions import PermissionDenied

        for obj in queryset.select_related('workspace__geodata_engine'):
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
                obj.delete()


# ======================================================================
# PHASE 4 — Layer Admin
# ======================================================================

@admin.register(Layer)
class LayerAdmin(admin.ModelAdmin):
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
    change_list_template = 'admin/geodata_engine/layer/change_list.html'

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
        ]
        return custom + super().get_urls()

    # ------------------------------------------------------------------
    # Computed list_display columns
    # ------------------------------------------------------------------
    def workspace_link(self, obj):
        url = f'/admin/geodata_engine/workspace/{obj.workspace_id}/change/'
        return format_html('<a href="{}">{}</a>', url, obj.workspace.name)
    workspace_link.short_description = 'Workspace'
    workspace_link.admin_order_field = 'workspace__name'

    def store_name(self, obj):
        url = f'/admin/geodata_engine/store/{obj.store_id}/change/'
        return format_html('<a href="{}">{}</a>', url, obj.store.name)
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
                        table_name=obj.table_name,
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

    # ------------------------------------------------------------------
    # 4.5 — Delete safety: GeoServer-first for PUBLISHED layers
    # ------------------------------------------------------------------
    def delete_model(self, request, obj):
        from django.core.exceptions import PermissionDenied

        if obj.publishing_state == 'PUBLISHED':
            engine = obj.workspace.geodata_engine if obj.workspace else None
            if engine:
                try:
                    client = EngineClientFactory.create_client(engine)
                    client.delete_layer(obj.workspace.name, obj.name)
                except Exception as exc:
                    raise PermissionDenied(
                        f"Cannot delete layer '{obj.name}': GeoServer deletion failed — {exc}"
                    )
                # Verify gone
                still_there = client.verify_featuretype(
                    workspace=obj.workspace.name,
                    store_name=obj.store.name,
                    table_name=obj.table_name,
                )
                if still_there:
                    raise PermissionDenied(
                        f"Cannot delete layer '{obj.name}': still exists in GeoServer after delete call."
                    )
        obj.delete()

    def delete_queryset(self, request, queryset):
        from django.core.exceptions import PermissionDenied

        for obj in queryset.select_related('workspace__geodata_engine', 'store'):
            if obj.publishing_state == 'PUBLISHED':
                engine = obj.workspace.geodata_engine if obj.workspace else None
                if engine:
                    try:
                        client = EngineClientFactory.create_client(engine)
                        client.delete_layer(obj.workspace.name, obj.name)
                    except Exception as exc:
                        self.message_user(
                            request,
                            f"Layer '{obj.name}' NOT deleted — GeoServer error: {exc}",
                            messages.ERROR,
                        )
                        continue
            obj.delete()

