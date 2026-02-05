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

# GeodataEngine Admin - Main hierarchical container
@admin.register(GeodataEngine)
class GeodataEngineAdmin(admin.ModelAdmin):
    form = GeodataEngineForm
    list_display = ['name', 'base_url', 'admin_username', 'is_active', 'is_default', 'manage_button', 'created_at']
    list_filter = ['is_active', 'is_default', 'created_at']
    search_fields = ['name', 'description', 'base_url']
    readonly_fields = ['id', 'geoserver_url', 'created_at', 'updated_at']
    
    # No inlines - we'll use custom detail view instead
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description')
        }),
        ('GeoServer Connection', {
            'fields': ('base_url', 'admin_username', 'admin_password'),
            'description': 'Connection details for GeoServer instance'
        }),
        ('Status', {
            'fields': ('is_active', 'is_default')
        }),
        ('Metadata', {
            'fields': ('id', 'geoserver_url', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description')
        }),
        ('GeoServer Connection', {
            'fields': ('base_url', 'admin_username', 'admin_password')
        }),
        ('Status', {
            'fields': ('is_active', 'is_default')
        }),
        ('Metadata', {
            'fields': ('id', 'geoserver_url', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # If creating new object
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
    
    def manage_button(self, obj):
        """Button to manage this GeoServer instance"""
        url = reverse('admin:geodata_engine_manage', args=[obj.pk])
        return format_html('<a class="button" href="{}">Manage</a>', url)
    manage_button.short_description = 'Actions'
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<uuid:engine_id>/manage/', self.admin_site.admin_view(self.manage_engine_view), name='geodata_engine_manage'),
            path('<uuid:engine_id>/sync/', self.admin_site.admin_view(self.sync_geoserver_view), name='geodata_engine_sync'),
            path('<uuid:engine_id>/workspace/add/', self.admin_site.admin_view(self.add_workspace_view), name='geodata_engine_add_workspace'),
            path('<uuid:engine_id>/store/add/', self.admin_site.admin_view(self.add_store_view), name='geodata_engine_add_store'),
            path('<uuid:engine_id>/layer/upload/', self.admin_site.admin_view(self.upload_layer_view), name='geodata_engine_upload_layer'),
        ]
        return custom_urls + urls
    
    def manage_engine_view(self, request, engine_id):
        """Custom view for managing a specific GeoServer engine"""
        engine = get_object_or_404(GeodataEngine, pk=engine_id)
        
        # Get related objects
        workspaces = engine.workspaces.all().order_by('name')
        stores = engine.stores.all().order_by('name')
        layers = Layer.objects.filter(workspace__geodata_engine=engine).order_by('workspace__name', 'name')
        
        context = {
            'title': f'Manage {engine.name}',
            'engine': engine,
            'workspaces': workspaces,
            'stores': stores,
            'layers': layers,
            'opts': self.model._meta,
            'has_view_permission': True,
            'has_change_permission': self.has_change_permission(request),
            'has_add_permission': self.has_add_permission(request),
        }
        
        return render(request, 'admin/geodata_engine/manage_engine.html', context)
    
    def add_workspace_view(self, request, engine_id):
        """View to add workspace to specific engine"""
        engine = get_object_or_404(GeodataEngine, pk=engine_id)
        
        if request.method == 'POST':
            form = WorkspaceForm(request.POST)
            if form.is_valid():
                workspace = form.save(commit=False)
                workspace.geodata_engine = engine
                workspace.created_by = request.user
                workspace.save()
                messages.success(request, f'Workspace "{workspace.name}" created successfully.')
                return HttpResponseRedirect(reverse('admin:geodata_engine_manage', args=[engine_id]))
        else:
            form = WorkspaceForm()
        
        context = {
            'title': f'Add Workspace to {engine.name}',
            'form': form,
            'engine': engine,
            'opts': Workspace._meta,
        }
        return render(request, 'admin/geodata_engine/add_workspace.html', context)
    
    def add_store_view(self, request, engine_id):
        """View to add store to specific engine"""
        engine = get_object_or_404(GeodataEngine, pk=engine_id)
        
        if request.method == 'POST':
            form = StoreForm(request.POST)
            if form.is_valid():
                store = form.save(commit=False)
                store.geodata_engine = engine
                store.created_by = request.user
                store.save()
                messages.success(request, f'Store "{store.name}" created successfully.')
                return HttpResponseRedirect(reverse('admin:geodata_engine_manage', args=[engine_id]))
        else:
            form = StoreForm()
        
        context = {
            'title': f'Add Store to {engine.name}',
            'form': form,
            'engine': engine,
            'opts': Store._meta,
        }
        return render(request, 'admin/geodata_engine/add_store.html', context)
    
    def upload_layer_view(self, request, engine_id):
        """View to upload layer to specific engine"""
        engine = get_object_or_404(GeodataEngine, pk=engine_id)
        workspaces = engine.workspaces.all()
        stores = engine.stores.all()
        
        if request.method == 'POST':
            form = LayerUploadForm(request.POST, request.FILES, workspaces=workspaces, stores=stores)
            if form.is_valid():
                try:
                    service = GeodataEngineService()
                    layer = service.upload_and_import_layer(
                        workspace_id=form.cleaned_data['workspace'].id,
                        layer_name=form.cleaned_data['layer_name'],
                        uploaded_file=form.cleaned_data['file'],
                        store_id=form.cleaned_data['store'].id,
                        user=request.user,
                        title=form.cleaned_data['title'],
                        description=form.cleaned_data['description']
                    )
                    messages.success(request, f'Layer "{layer.name}" uploaded successfully.')
                    return HttpResponseRedirect(reverse('admin:geodata_engine_manage', args=[engine_id]))
                except Exception as e:
                    messages.error(request, f'Failed to upload layer: {e}')
        else:
            form = LayerUploadForm(workspaces=workspaces, stores=stores)
        
        context = {
            'title': f'Upload Layer to {engine.name}',
            'form': form,
            'engine': engine,
            'opts': Layer._meta,
        }
        return render(request, 'admin/geodata_engine/upload_layer.html', context)
    
    def sync_geoserver_view(self, request, engine_id):
        """Sync Django DB with GeoServer resources - Direct sync without confirmation"""
        engine = get_object_or_404(GeodataEngine, pk=engine_id)
        
        # Direct sync for both GET and POST requests - no confirmation needed
        try:
            sync_service = GeoServerSyncService(engine)
            results = sync_service.sync_all_resources(created_by=request.user)
            
            if results.get('success'):
                messages.success(request, f"""
                    🎯 Sync completed successfully!
                    
                    📁 Workspaces: {results['workspaces']['synced']} synced, {results['workspaces']['created']} created, {results['workspaces']['deleted']} deleted
                    🗃️ Stores: {results['stores']['synced']} synced, {results['stores']['created']} created, {results['stores']['deleted']} deleted  
                    📊 Layers: {results['layers']['synced']} synced, {results['layers']['created']} created, {results['layers']['deleted']} deleted
                    
                    ✅ Django now matches GeoServer exactly!
                """)
            else:
                messages.error(request, f"Sync failed: {results.get('error', 'Unknown error')}")
            
            return HttpResponseRedirect(reverse('admin:geodata_engine_manage', args=[engine_id]))
            
        except Exception as e:
            messages.error(request, f'Sync failed: {e}')
            return HttpResponseRedirect(reverse('admin:geodata_engine_manage', args=[engine_id]))

# Hidden admin classes - only accessible through GeodataEngine management
# These are hidden from main admin but still available for direct access if needed

class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'geodata_engine', 'created_at']
    list_filter = ['geodata_engine']
    search_fields = ['name', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    def has_module_permission(self, request):
        return False  # Hide from main admin
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

class StoreAdmin(admin.ModelAdmin):
    list_display = ['name', 'geodata_engine', 'host', 'database', 'schema', 'created_at']
    list_filter = ['geodata_engine', 'host', 'schema']
    search_fields = ['name', 'description', 'host', 'database']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    def has_module_permission(self, request):
        return False  # Hide from main admin
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

class LayerAdmin(admin.ModelAdmin):
    list_display = ['name', 'workspace', 'store', 'publishing_state', 'geometry_type', 'created_at']
    list_filter = ['workspace__geodata_engine', 'workspace', 'store', 'publishing_state', 'geometry_type']
    search_fields = ['name', 'title', 'description', 'table_name']
    readonly_fields = ['id', 'full_table_name', 'published_url', 'created_at', 'updated_at']
    
    def has_module_permission(self, request):
        return False  # Hide from main admin
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

# Register hidden admins
admin.site.register(Workspace, WorkspaceAdmin)
admin.site.register(Store, StoreAdmin)
admin.site.register(Layer, LayerAdmin)