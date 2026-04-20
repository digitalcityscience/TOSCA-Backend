"""
Active Engine Selector Widget for Admin Header
"""
from django import template
from django.utils.html import format_html
from django.urls import reverse
from ..middleware import get_active_engine
from ..models import GeodataEngine

register = template.Library()


@register.inclusion_tag('admin/geodata_engine/engine_selector.html', takes_context=True)
def engine_selector(context):
    """Render engine selector dropdown in admin header"""
    request = context['request']
    active_engine = get_active_engine(request)
    all_engines = GeodataEngine.objects.filter(is_active=True).order_by('name')
    
    return {
        'active_engine': active_engine,
        'all_engines': all_engines,
        'request': request,
    }


@register.simple_tag(takes_context=True)
def engine_switch_url(context, engine_id):
    """Generate URL to switch to different engine"""
    request = context['request']
    current_path = request.get_full_path()
    
    # Add or update switch_engine parameter
    separator = '&' if '?' in current_path else '?'
    if 'switch_engine=' in current_path:
        # Replace existing switch_engine parameter
        import re
        pattern = r'switch_engine=[^&]*'
        current_path = re.sub(pattern, f'switch_engine={engine_id}', current_path)
        return current_path
    else:
        # Add new switch_engine parameter
        return f"{current_path}{separator}switch_engine={engine_id}"


@register.filter
def engine_status_icon(engine):
    """Return status icon for engine"""
    if not engine:
        return "❓"
    
    if not engine.is_active:
        return "⚫"  # Inactive
    
    if engine.is_default:
        return "🎯"  # Default
    
    # TODO: Add connection status check
    return "✅"  # Active


@register.simple_tag(takes_context=True)
def show_engine_selector(context):
    """Check if we should show engine selector"""
    request = context['request']
    # Only show in admin pages
    return request.path.startswith('/admin/')