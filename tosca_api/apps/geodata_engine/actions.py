"""
Dynamic admin actions based on active engine and plugins
"""
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.contrib.admin import helpers
from django.template.response import TemplateResponse
from django.contrib.admin.utils import model_ngettext
from .middleware import get_active_engine
from .plugins import plugin_registry
from .sync_service import GeoServerSyncService


@admin.action(description="🔄 Sync with active GeodataEngine")
def sync_with_active_engine(modeladmin, request, queryset):
    """Universal sync action that works with any engine type"""
    active_engine = get_active_engine(request)
    if not active_engine:
        messages.error(request, "No active GeodataEngine selected. Please select one first.")
        return
    
    # Get appropriate plugin for the active engine
    plugin = plugin_registry.get_plugin(active_engine.engine_type or 'geoserver')
    if not plugin:
        messages.error(request, f"No plugin found for engine type: {active_engine.engine_type}")
        return
    
    try:
        sync_service = plugin.get_sync_service(active_engine)
        results = sync_service.sync_all_resources(created_by=request.user)
        
        if results.get('success'):
            messages.success(request, f"""
                🎯 Sync completed with {active_engine.name}!
                
                📁 Workspaces: {results['workspaces']['synced']} synced, {results['workspaces']['created']} created, {results['workspaces']['deleted']} deleted
                🗃️ Stores: {results['stores']['synced']} synced, {results['stores']['created']} created, {results['stores']['deleted']} deleted  
                📊 Layers: {results['layers']['synced']} synced, {results['layers']['created']} created, {results['layers']['deleted']} deleted
            """)
        else:
            messages.error(request, f"Sync failed: {results.get('error', 'Unknown error')}")
            
    except Exception as e:
        messages.error(request, f'Sync failed: {e}')


@admin.action(description="🔍 Test connection to active engine")
def test_active_engine_connection(modeladmin, request, queryset):
    """Test connection to active engine"""
    active_engine = get_active_engine(request)
    if not active_engine:
        messages.error(request, "No active GeodataEngine selected.")
        return
    
    plugin = plugin_registry.get_plugin(active_engine.engine_type or 'geoserver')
    if not plugin:
        messages.error(request, f"No plugin found for engine type: {active_engine.engine_type}")
        return
    
    result = plugin.validate_connection(active_engine)
    if result['valid']:
        messages.success(request, f"✅ Connection to {active_engine.name} successful: {result['message']}")
    else:
        messages.error(request, f"❌ Connection to {active_engine.name} failed: {result['message']}")


@admin.action(description="📤 Upload layer to active engine")
def upload_layer_to_active_engine(modeladmin, request, queryset):
    """Upload layer action - redirect to form"""
    active_engine = get_active_engine(request)
    if not active_engine:
        messages.error(request, "No active GeodataEngine selected.")
        return
    
    # For now, redirect to a simple form (can be enhanced)
    messages.info(request, f"Redirect to upload form for {active_engine.name}")
    # TODO: Implement upload form or inline action


# GeoServer specific actions
def sync_with_geoserver(modeladmin, request, queryset):
    """GeoServer specific sync action"""
    return sync_with_active_engine(modeladmin, request, queryset)


def test_geoserver_connection(modeladmin, request, queryset):
    """GeoServer specific connection test"""
    return test_active_engine_connection(modeladmin, request, queryset)


# Action registry for dynamic loading
def get_actions_for_model(model_name, request):
    """Get appropriate actions based on active engine and model"""
    active_engine = get_active_engine(request)
    base_actions = [
        sync_with_active_engine,
        test_active_engine_connection,
    ]
    
    if not active_engine:
        return base_actions
    
    # Get plugin-specific actions
    plugin = plugin_registry.get_plugin(active_engine.engine_type or 'geoserver')
    if plugin:
        plugin_actions = plugin.get_admin_actions()
        # Convert plugin actions to admin action functions
        for action_name, action_func in plugin_actions.items():
            base_actions.append(action_func)
    
    # Add model-specific actions
    if model_name == 'Layer':
        base_actions.append(upload_layer_to_active_engine)
    
    return base_actions