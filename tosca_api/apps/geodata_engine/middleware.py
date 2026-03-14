"""
Active GeodataEngine Session Management
----------------------------------------
HISTORY: This was written for the Django admin panel before the geo_console app
existed. It tracked which engine was "active" in session so admin list views
could filter Workspace/Store/Layer records by that engine.

STATUS (14 March 2026): INACTIVE
- Removed from MIDDLEWARE and CONTEXT_PROCESSORS in settings/base.py
- admin.py no longer calls get_active_engine()
- engine_selector.html template is in backup_unused/

FUTURE (geo_console Phase 2+):
An equivalent concept will be implemented inside geo_console — but using
URL-based engine selection, not session state:
  /console/workspaces/?engine=<uuid>
  /console/stores/?engine=<uuid>
The get_active_engine() helper below can be adapted there if needed.
"""
from django.utils.deprecation import MiddlewareMixin
from .models import GeodataEngine


class ActiveEngineMiddleware(MiddlewareMixin):
    """Middleware to manage active GeodataEngine in session"""
    
    def process_request(self, request):
        # Only apply to admin requests
        if not request.path.startswith('/admin/'):
            return
        
        # Check if user wants to switch engine
        switch_engine_id = request.GET.get('switch_engine')
        if switch_engine_id:
            try:
                engine = GeodataEngine.objects.get(id=switch_engine_id, is_active=True)
                request.session['active_geodata_engine_id'] = str(engine.id)
                request.session['active_geodata_engine_name'] = engine.name
            except GeodataEngine.DoesNotExist:
                pass
        
        # Ensure we have an active engine in session
        if not request.session.get('active_geodata_engine_id'):
            # Get default engine or first active engine
            default_engine = GeodataEngine.objects.filter(is_default=True, is_active=True).first()
            if not default_engine:
                default_engine = GeodataEngine.objects.filter(is_active=True).first()
            
            if default_engine:
                request.session['active_geodata_engine_id'] = str(default_engine.id)
                request.session['active_geodata_engine_name'] = default_engine.name


def get_active_engine(request):
    """Helper function to get active engine from session"""
    engine_id = request.session.get('active_geodata_engine_id')
    if engine_id:
        try:
            return GeodataEngine.objects.get(id=engine_id, is_active=True)
        except GeodataEngine.DoesNotExist:
            pass
    return None


def active_engine_context(request):
    """Context processor for active engine info"""
    if not request.path.startswith('/admin/'):
        return {}
    
    active_engine = get_active_engine(request)
    all_engines = GeodataEngine.objects.filter(is_active=True).order_by('name')
    
    return {
        'active_geodata_engine': active_engine,
        'all_geodata_engines': all_engines,
    }