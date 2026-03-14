from django.contrib import admin
from django import forms
from django.utils.html import format_html
from django.contrib import messages
from django.contrib.admin import SimpleListFilter
from .engine_factory import EngineClientFactory
from .models import GeodataEngine, Workspace, Store, Layer


# Admin Forms
class GeodataEngineForm(forms.ModelForm):
    class Meta:
        model = GeodataEngine
        fields = '__all__'
        widgets = {
            'admin_password': forms.PasswordInput(render_value=True),
            'base_url': forms.TextInput(attrs={'size': 60}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'api_key': forms.PasswordInput(render_value=True),
        }
    
class ActiveEngineFilter(SimpleListFilter):
    """Filter to show only resources for active engine"""
    title = 'geodata engine'
    parameter_name = 'engine'
    
    def lookups(self, request, model_admin):
        engines = GeodataEngine.objects.filter(is_active=True)
        return [(str(engine.id), engine.name) for engine in engines]
    
    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(geodata_engine_id=self.value())
        return queryset


# GeodataEngine Admin - Engine Management
@admin.register(GeodataEngine)
class GeodataEngineAdmin(admin.ModelAdmin):
    form = GeodataEngineForm
    list_display = ['name', 'engine_type', 'base_url', 'is_active', 'is_default', 'connection_status', 'created_at']
    list_filter = ['engine_type', 'is_active', 'is_default', 'created_at']
    search_fields = ['name', 'description', 'base_url']
    readonly_fields = ['id', 'geoserver_url', 'connection_status', 'created_at', 'updated_at']
    actions = ['check_all_connections']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'engine_type')
        }),
        ('Connection Details', {
            'fields': ('base_url', 'admin_username', 'admin_password', 'api_key'),
            'description': 'Connection details vary by engine type'
        }),
        ('Status', {
            'fields': ('is_active', 'is_default', 'connection_status')
        }),
        ('Metadata', {
            'fields': ('id', 'geoserver_url', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def connection_status(self, obj):
        """Show connection status with real-time check"""
        try:
            client = EngineClientFactory.create_client(obj)
            _ = client.get_workspaces()
            return format_html('<span style="color: green; font-weight: bold;">✅ Online</span>')
        except Exception as e:
            return format_html(
                '<span style="color: red; font-weight: bold;">❌ Offline</span><br/><small style="color: #666;">{}</small>',
                str(e)[:50],
            )
    connection_status.short_description = 'Connection Status'
    
    @admin.action(description="🔍 Check connection status of all engines")
    def check_all_connections(self, request, queryset):
        """Auto-check all selected engines' connection status"""
        checked_count = 0
        online_count = 0
        offline_count = 0
        
        for engine in queryset:
            checked_count += 1
            try:
                client = EngineClientFactory.create_client(engine)
                _ = client.get_workspaces()
                online_count += 1
            except Exception:
                offline_count += 1
        
        messages.info(request, f"📊 Connection Check Results: {checked_count} engines checked, {online_count} online, {offline_count} offline")
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
    
    def get_exclude(self, request, obj=None):
        """Hide created_by field from form"""
        return ['created_by']
    
# Session-Filtered Workspace Admin
@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'geodata_engine', 'description', 'created_at']
    list_filter = [ActiveEngineFilter, 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fields = ['geodata_engine', 'name', 'description']  # Explicitly specify fields
    
    def get_readonly_fields(self, request, obj=None):
        """Make geodata_engine readonly when editing existing workspace"""
        readonly = list(self.readonly_fields)
        if obj:  # Editing existing object
            readonly.append('geodata_engine')
        return readonly    
    def get_queryset(self, request):
        """Show all workspaces for all engines — no session filter."""
        return super().get_queryset(request)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'geodata_engine' in form.base_fields:
            form.base_fields['geodata_engine'].queryset = (
                GeodataEngine.objects.filter(is_active=True).order_by('name')
            )
        return form

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
    
    def delete_model(self, request, obj):
        """Custom delete method to also delete from GeoServer"""
        obj.delete()  # This will call our custom model delete method
    
    def delete_queryset(self, request, queryset):
        """Custom bulk delete method to also delete from GeoServer"""
        for obj in queryset:
            obj.delete()  # Call individual delete for each object
    
# Store Form with Dynamic Fields
class StoreForm(forms.ModelForm):
    class Meta:
        model = Store
        fields = '__all__'
        widgets = {
            'password': forms.PasswordInput(render_value=True),
            'description': forms.Textarea(attrs={'rows': 3}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make fields conditional based on store type
        if self.instance and self.instance.pk:
            self._make_fields_conditional()
    
    def _make_fields_conditional(self):
        """Make fields required/optional based on store type"""
        store_type = self.instance.store_type
        
        # PostGIS fields
        postgis_fields = ['host', 'port', 'database', 'username', 'password', 'schema']
        # File fields  
        file_fields = ['file_path', 'charset']
        
        if store_type == 'postgis':
            for field in postgis_fields:
                if field in self.fields:
                    self.fields[field].required = True
            for field in file_fields:
                if field in self.fields:
                    self.fields[field].required = False
        elif store_type in ['file', 'geotiff']:
            for field in file_fields:
                if field in self.fields:
                    self.fields[field].required = True
            for field in postgis_fields:
                if field in self.fields:
                    self.fields[field].required = False


# Session-Filtered Store Admin  
@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    form = StoreForm
    list_display = ['name', 'store_type', 'workspace', 'geodata_engine', 'connection_info', 'created_at']
    list_filter = [ActiveEngineFilter, 'store_type', 'workspace', 'created_at']
    search_fields = ['name', 'description', 'host', 'database', 'file_path']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('geodata_engine', 'workspace', 'name', 'store_type', 'description')
        }),
        ('PostGIS Configuration', {
            'fields': ('host', 'port', 'database', 'username', 'password', 'schema'),
            'classes': ('collapse',),
            'description': 'Required for PostGIS stores'
        }),
        ('File-based Configuration', {
            'fields': ('file_path', 'charset'),
            'classes': ('collapse',),
            'description': 'Required for file-based and GeoTIFF stores'
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    
    def connection_info(self, obj):
        """Display connection info based on store type"""
        if obj.store_type == 'postgis':
            return f"{obj.host}:{obj.port}/{obj.database}"
        elif obj.store_type in ['file', 'geotiff']:
            return obj.file_path or "No path set"
        return "N/A"
    connection_info.short_description = "Connection Info"
    
    def get_readonly_fields(self, request, obj=None):
        """Make geodata_engine readonly when editing existing store"""
        readonly = list(self.readonly_fields)
        if obj:  # Editing existing object
            readonly.append('geodata_engine')
        return readonly    
    def get_queryset(self, request):
        """Show all stores for all engines — no session filter."""
        return super().get_queryset(request)

    def get_form(self, request, obj=None, **kwargs):
        """Show all active engines and all workspaces — no session filter."""
        form = super().get_form(request, obj, **kwargs)
        if 'geodata_engine' in form.base_fields:
            form.base_fields['geodata_engine'].queryset = (
                GeodataEngine.objects.filter(is_active=True).order_by('name')
            )
        if 'workspace' in form.base_fields:
            form.base_fields['workspace'].queryset = Workspace.objects.select_related(
                'geodata_engine'
            ).all().order_by('geodata_engine__name', 'name')
        return form
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


# Session-Filtered Layer Admin
@admin.register(Layer)
class LayerAdmin(admin.ModelAdmin):
    list_display = ['name', 'workspace', 'store', 'active_engine_indicator', 'publishing_state', 'geometry_type', 'srid', 'created_at']
    list_filter = [ActiveEngineFilter, 'workspace', 'store', 'publishing_state', 'geometry_type', 'created_at']
    search_fields = ['name', 'title', 'description', 'table_name']
    readonly_fields = ['id', 'active_engine_indicator', 'full_table_name', 'published_url', 'created_at', 'updated_at']
    exclude = ['created_by']  # Hide created_by field (Layer doesn't have geodata_engine)
    
    # Form fields - exclude geodata_engine (auto-set from active engine)
    fields = ['workspace', 'store', 'name', 'title', 'description', 'table_name', 
              'geometry_column', 'geometry_type', 'srid', 'publishing_state']
    
    def active_engine_indicator(self, obj):
        """Show which engine this layer belongs to"""
        if obj.workspace and obj.workspace.geodata_engine:
            return format_html(
                '<div style="display: inline-block;">'
                '<span style="background: linear-gradient(135deg, #417690, #5a9bc4); color: white; padding: 4px 10px; '
                'border-radius: 12px; font-size: 0.85em; font-weight: 500; text-shadow: 0 1px 2px rgba(0,0,0,0.1); '
                'box-shadow: 0 2px 4px rgba(0,0,0,0.1);">{}</span>'
                '</div>',
                obj.workspace.geodata_engine.name
            )
        return format_html('<span style="color: #999; font-style: italic;">No Engine</span>')
    active_engine_indicator.short_description = 'Engine'
    active_engine_indicator.admin_order_field = 'workspace__geodata_engine'
    
    def get_queryset(self, request):
        """Show all layers for all engines — no session filter."""
        return super().get_queryset(request)
    
    def get_form(self, request, obj=None, **kwargs):
        """Show all workspaces and stores for all engines — no session filter."""
        form = super().get_form(request, obj, **kwargs)
        if 'workspace' in form.base_fields:
            form.base_fields['workspace'].queryset = Workspace.objects.select_related(
                'geodata_engine'
            ).all().order_by('geodata_engine__name', 'name')
        if 'store' in form.base_fields:
            form.base_fields['store'].queryset = Store.objects.select_related(
                'workspace__geodata_engine'
            ).all().order_by('workspace__geodata_engine__name', 'name')
        return form
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
    
