from django.contrib import admin
from django import forms
from django.utils.html import format_html
from django.contrib import messages
from django.contrib.admin import SimpleListFilter
from .models import GeodataEngine, Workspace, Store, Layer
from .middleware import get_active_engine
from .actions import get_actions_for_model, sync_with_active_engine, test_active_engine_connection
from .plugins import plugin_registry
import json


# Admin Forms with Plugin Support
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
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set engine type choices from plugin registry
        self.fields['engine_type'].choices = plugin_registry.get_engine_choices()


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
    actions = [sync_with_active_engine, test_active_engine_connection, 'check_all_connections']
    
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
        plugin = plugin_registry.get_plugin(obj.engine_type or 'geoserver')
        if plugin:
            result = plugin.validate_connection(obj)
            if result['valid']:
                return format_html('<span style="color: green; font-weight: bold;">✅ Online</span>')
            else:
                return format_html('<span style="color: red; font-weight: bold;">❌ Offline</span><br/><small style="color: #666;">{}</small>', result['message'][:50])
        return format_html('<span style="color: orange;">❓ Unknown</span>')
    connection_status.short_description = 'Connection Status'
    
    @admin.action(description="🔍 Check connection status of all engines")
    def check_all_connections(self, request, queryset):
        """Auto-check all selected engines' connection status"""
        checked_count = 0
        online_count = 0
        offline_count = 0
        
        for engine in queryset:
            plugin = plugin_registry.get_plugin(engine.engine_type or 'geoserver')
            if plugin:
                result = plugin.validate_connection(engine)
                checked_count += 1
                if result['valid']:
                    online_count += 1
                else:
                    offline_count += 1
        
        messages.info(request, f"📊 Connection Check Results: {checked_count} engines checked, {online_count} online, {offline_count} offline")
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
    
    def get_exclude(self, request, obj=None):
        """Hide created_by field from form"""
        return ['created_by']
    
    def get_actions(self, request):
        """Dynamic actions based on plugins"""
        actions = super().get_actions(request)
        
        # Add plugin-specific actions
        for plugin in plugin_registry.get_all_plugins().values():
            plugin_actions = plugin.get_admin_actions()
            for action_name, action_func in plugin_actions.items():
                actions[action_name] = (action_func, action_name, action_func.__doc__ or action_name)
        
        return actions


# Session-Filtered Workspace Admin
@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'geodata_engine', 'description', 'created_at']
    list_filter = [ActiveEngineFilter, 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fields = ['geodata_engine', 'name', 'description']  # Explicitly specify fields
    actions = [sync_with_active_engine]
    
    def get_readonly_fields(self, request, obj=None):
        """Make geodata_engine readonly when editing existing workspace"""
        readonly = list(self.readonly_fields)
        if obj:  # Editing existing object
            readonly.append('geodata_engine')
        return readonly    
    def get_queryset(self, request):
        """Filter by active engine from session"""
        qs = super().get_queryset(request)
        active_engine = get_active_engine(request)
        if active_engine:
            return qs.filter(geodata_engine=active_engine)
        return qs
    
    def get_form(self, request, obj=None, **kwargs):
        """Configure geodata_engine field: selectable when adding, readonly when editing"""
        form = super().get_form(request, obj, **kwargs)
        
        # Filter geodata_engine choices to active engines only
        if 'geodata_engine' in form.base_fields:
            form.base_fields['geodata_engine'].queryset = GeodataEngine.objects.filter(is_active=True).order_by('name')
            
            if not obj:  # Adding new workspace
                active_engine = get_active_engine(request)
                if active_engine:
                    form.base_fields['geodata_engine'].initial = active_engine
                    # Keep it visible and selectable for new objects
        # For editing: geodata_engine will be readonly (handled by get_readonly_fields)
        
        return form
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            # Always set geodata_engine to active engine
            active_engine = get_active_engine(request)
            if active_engine:
                obj.geodata_engine = active_engine
        super().save_model(request, obj, form, change)
    
    def delete_model(self, request, obj):
        """Custom delete method to also delete from GeoServer"""
        obj.delete()  # This will call our custom model delete method
    
    def delete_queryset(self, request, queryset):
        """Custom bulk delete method to also delete from GeoServer"""
        for obj in queryset:
            obj.delete()  # Call individual delete for each object
    
    def get_actions(self, request):
        """Get actions for workspace model"""
        actions = super().get_actions(request)
        dynamic_actions = get_actions_for_model('Workspace', request)
        for action in dynamic_actions:
            actions[action.__name__] = (action, action.__name__, action.__doc__ or action.__name__)
        return actions


# Session-Filtered Store Admin  
@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ['name', 'workspace', 'geodata_engine', 'host', 'database', 'schema', 'created_at']
    list_filter = [ActiveEngineFilter, 'workspace', 'host', 'schema', 'created_at']
    search_fields = ['name', 'description', 'host', 'database']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fields = ['geodata_engine', 'workspace', 'name', 'host', 'port', 'database', 'username', 'password', 'schema', 'description']  # Explicitly specify fields
    actions = [sync_with_active_engine]
    
    def get_readonly_fields(self, request, obj=None):
        """Make geodata_engine readonly when editing existing store"""
        readonly = list(self.readonly_fields)
        if obj:  # Editing existing object
            readonly.append('geodata_engine')
        return readonly    
    def get_queryset(self, request):
        """Filter by active engine from session"""
        qs = super().get_queryset(request)
        active_engine = get_active_engine(request)
        if active_engine:
            return qs.filter(geodata_engine=active_engine)
        return qs
    
    def get_form(self, request, obj=None, **kwargs):
        """Configure geodata_engine and workspace fields: selectable when adding, readonly when editing"""
        form = super().get_form(request, obj, **kwargs)
        active_engine = get_active_engine(request)
        
        if active_engine:
            # Always filter workspace choices by active engine
            if 'workspace' in form.base_fields:
                form.base_fields['workspace'].queryset = Workspace.objects.filter(geodata_engine=active_engine)
            
            # Filter geodata_engine choices to active engines only  
            if 'geodata_engine' in form.base_fields:
                form.base_fields['geodata_engine'].queryset = GeodataEngine.objects.filter(is_active=True).order_by('name')
                
                if not obj:  # Adding new store
                    form.base_fields['geodata_engine'].initial = active_engine
                    # Keep it visible and selectable for new objects
        # For editing: geodata_engine will be readonly (handled by get_readonly_fields)
        
        return form
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            # Always set geodata_engine to active engine
            active_engine = get_active_engine(request)
            if active_engine:
                obj.geodata_engine = active_engine
        super().save_model(request, obj, form, change)


# Session-Filtered Layer Admin
@admin.register(Layer)
class LayerAdmin(admin.ModelAdmin):
    list_display = ['name', 'workspace', 'store', 'active_engine_indicator', 'publishing_state', 'geometry_type', 'srid', 'created_at']
    list_filter = [ActiveEngineFilter, 'workspace', 'store', 'publishing_state', 'geometry_type', 'created_at']
    search_fields = ['name', 'title', 'description', 'table_name']
    readonly_fields = ['id', 'active_engine_indicator', 'full_table_name', 'published_url', 'created_at', 'updated_at']
    exclude = ['created_by']  # Hide created_by field (Layer doesn't have geodata_engine)
    actions = [sync_with_active_engine]
    
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
        """Filter by active engine from session"""
        qs = super().get_queryset(request)
        active_engine = get_active_engine(request)
        if active_engine:
            return qs.filter(workspace__geodata_engine=active_engine)
        return qs
    
    def get_form(self, request, obj=None, **kwargs):
        """Filter workspace and store choices by active engine"""
        form = super().get_form(request, obj, **kwargs)
        active_engine = get_active_engine(request)
        
        if active_engine:
            if 'workspace' in form.base_fields:
                form.base_fields['workspace'].queryset = Workspace.objects.filter(geodata_engine=active_engine)
            if 'store' in form.base_fields:
                form.base_fields['store'].queryset = Store.objects.filter(geodata_engine=active_engine)
            
            # Note: Layer doesn't have direct geodata_engine field, it's through workspace
        return form
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
    
    def get_actions(self, request):
        """Get actions for layer model"""
        actions = super().get_actions(request)
        dynamic_actions = get_actions_for_model('Layer', request)
        for action in dynamic_actions:
            actions[action.__name__] = (action, action.__name__, action.__doc__ or action.__name__)
        return actions