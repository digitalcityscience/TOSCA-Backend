from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import TemplateView, FormView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.contrib import messages
from django.test import Client
from django.urls import reverse, reverse_lazy
from django import forms
from django.core.exceptions import ValidationError
import logging
import json
import re

logger = logging.getLogger(__name__)

def validate_geoserver_url(value):
    """Custom validator for GeoServer URLs that accepts Docker container names"""
    # Allow http:// or https:// with any hostname (including Docker container names)
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'[a-zA-Z0-9._-]+(?::[0-9]+)?'  # hostname:port (allows container names)
        r'(?:/.*)?$'  # optional path
    )
    if not url_pattern.match(value):
        raise ValidationError('Enter a valid URL (e.g., http://geoserver:8080/geoserver)')

# Form classes for console editing
class EngineForm(forms.Form):
    """Form for editing engines in console"""
    name = forms.CharField(max_length=255)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
    base_url = forms.CharField(
        max_length=500,
        label='GeoServer URL',
        validators=[validate_geoserver_url],
        help_text='e.g., http://geoserver:8080/geoserver or http://localhost:8080/geoserver'
    )
    admin_username = forms.CharField(max_length=100, label='Username')
    admin_password = forms.CharField(widget=forms.PasswordInput(), required=False, label='Password')
    is_active = forms.BooleanField(required=False)
    is_default = forms.BooleanField(required=False)

class WorkspaceForm(forms.Form):
    """Form for editing workspaces in console"""
    name = forms.CharField(max_length=100)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
    is_active = forms.BooleanField(required=False)

class StoreForm(forms.Form):
    """Form for editing stores in console"""
    name = forms.CharField(max_length=100)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
    store_type = forms.ChoiceField(choices=[
        ('postgis', 'PostGIS'),
        ('shapefile', 'Shapefile'),
        ('geotiff', 'GeoTIFF'),
    ])
    connection_params = forms.CharField(widget=forms.Textarea(attrs={'rows': 5}), required=False)
    is_active = forms.BooleanField(required=False)

class LayerForm(forms.Form):
    """Form for editing layers in console"""
    name = forms.CharField(max_length=100)
    title = forms.CharField(max_length=255, required=False)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
    is_active = forms.BooleanField(required=False)

class ConsoleBaseView(LoginRequiredMixin, TemplateView):
    """Base view for all console views with common context"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_client = Client()
    
    def get_api_data(self, endpoint):
        """Helper method to make internal API calls"""
        try:
            # Force login for API calls
            self.api_client.force_login(self.request.user)
            response = self.api_client.get(endpoint)
            
            if response.status_code == 200:
                return json.loads(response.content), None
            else:
                return None, f"API Error: {response.status_code}"
        except Exception as e:
            logger.error(f"API call failed for {endpoint}: {e}")
            return None, f"API call failed: {str(e)}"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get engines via API
        engines_data, error = self.get_api_data('/api/geodata/engines/')
        if engines_data:
            context['engines'] = engines_data.get('results', engines_data)
        else:
            context['engines'] = []
            if error:
                messages.warning(self.request, f"Could not load engines: {error}")
        
        context['current_section'] = getattr(self, 'section', 'dashboard')
        return context

class IndexView(ConsoleBaseView):
    template_name = 'console/index.html'
    section = 'dashboard'

class EnginesView(ConsoleBaseView):
    template_name = 'console/engines.html'
    section = 'engines'

# TODO: Workspace, Store, Layer views - to be implemented later
# class WorkspacesView(ConsoleBaseView):
#     template_name = 'console/workspaces.html'
#     section = 'workspaces'
#     
#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         
#         # Get workspaces via API
#         workspaces_data, error = self.get_api_data('/api/geodata/workspaces/')
#         if workspaces_data:
#             context['workspaces'] = workspaces_data.get('results', workspaces_data)
#         else:
#             context['workspaces'] = []
#             if error:
#                 messages.warning(self.request, f"Could not load workspaces: {error}")
#         
#         return context

class StoresView(ConsoleBaseView):
    template_name = 'console/stores.html'
    section = 'stores'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get stores via API
        stores_data, error = self.get_api_data('/api/geodata/stores/')
        if stores_data:
            context['stores'] = stores_data.get('results', stores_data)
        else:
            context['stores'] = []
            if error:
                messages.warning(self.request, f"Could not load stores: {error}")
        
        return context

class LayersView(ConsoleBaseView):
    template_name = 'console/layers.html'
    section = 'layers'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get layers via API
        layers_data, error = self.get_api_data('/api/geodata/layers/')
        if layers_data:
            context['layers'] = layers_data.get('results', layers_data)
        else:
            context['layers'] = []
            if error:
                messages.warning(self.request, f"Could not load layers: {error}")
        
        return context

class StylesView(ConsoleBaseView):
    template_name = 'console/styles.html' 
    section = 'styles'

class SyncView(ConsoleBaseView):
    template_name = 'console/sync.html'
    section = 'sync'

class JobsView(ConsoleBaseView):
    template_name = 'console/jobs.html'
    section = 'jobs'

@login_required
def sync_engines(request):
    """Sync engines using API call"""
    if request.method == 'POST':
        try:
            # Use test client for internal API call
            client = Client()
            client.force_login(request.user)
            
            # Call sync_all API endpoint
            response = client.post('/api/geodata/engines/sync_all/')
            
            if response.status_code == 200:
                results = json.loads(response.content)
                messages.success(request, results.get('message', 'Sync completed successfully'))
            else:
                error_data = json.loads(response.content) if response.content else {}
                messages.error(request, error_data.get('message', f'Sync failed with status {response.status_code}'))
                
        except Exception as e:
            logger.error(f"Sync API call failed: {e}")
            messages.error(request, f'Sync failed: {str(e)}')
    
    return redirect('console:engines')

class BaseConsoleEditView(LoginRequiredMixin, FormView):
    """Base class for console edit/create views"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_client = Client()
    
    def get_api_data(self, endpoint, method='GET', data=None):
        """Helper method to make API calls"""
        try:
            self.api_client.force_login(self.request.user)
            
            if method == 'GET':
                response = self.api_client.get(endpoint)
            elif method == 'POST':
                response = self.api_client.post(endpoint, data=data, content_type='application/json')
            elif method == 'PUT':
                response = self.api_client.put(endpoint, data=data, content_type='application/json')
            elif method == 'PATCH':
                response = self.api_client.patch(endpoint, data=data, content_type='application/json')
            
            if response.status_code in [200, 201]:
                return json.loads(response.content) if response.content else {}, None
            else:
                error_data = json.loads(response.content) if response.content else {}
                return None, error_data.get('detail', f'API Error: {response.status_code}')
        except Exception as e:
            logger.error(f"API call failed for {endpoint}: {e}")
            return None, f"API call failed: {str(e)}"

class EngineEditView(BaseConsoleEditView):
    """Edit existing engine in console"""
    template_name = 'console/engine_form.html'
    form_class = EngineForm
    
    def get_success_url(self):
        return reverse('console:engines')
    
    def get_initial(self):
        """Load current engine data from API"""
        engine_id = self.kwargs['pk']
        data, error = self.get_api_data(f'/api/geodata/engines/{engine_id}/')
        if data:
            return {
                'name': data.get('name'),
                'description': data.get('description'),
                'base_url': data.get('base_url'),
                'admin_username': data.get('admin_username'),
                'is_active': data.get('is_active'),
                'is_default': data.get('is_default'),
            }
        return {}
    
    def form_valid(self, form):
        """Update engine via API"""
        engine_id = self.kwargs['pk']
        update_data = {
            'name': form.cleaned_data['name'],
            'description': form.cleaned_data['description'],
            'base_url': form.cleaned_data['base_url'],
            'admin_username': form.cleaned_data['admin_username'],
            'is_active': form.cleaned_data['is_active'],
            'is_default': form.cleaned_data['is_default'],
        }
        
        # Add password only if provided
        if form.cleaned_data['admin_password']:
            update_data['admin_password'] = form.cleaned_data['admin_password']
        
        logger.info(f"Updating engine {engine_id} with data: {update_data}")
        
        data, error = self.get_api_data(f'/api/geodata/engines/{engine_id}/', 'PATCH', json.dumps(update_data))
        
        if data:
            messages.success(self.request, 'Engine updated successfully')
            return redirect(self.get_success_url())
        else:
            logger.error(f"Engine update failed: {error}")
            messages.error(self.request, f'Failed to update engine: {error}')
            return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'Edit'
        context['object_type'] = 'Engine'
        return context

class EngineCreateView(BaseConsoleEditView):
    """Create new engine in console"""
    template_name = 'console/engine_form.html'
    form_class = EngineForm
    
    def get_success_url(self):
        return reverse('console:engines')
    
    def form_valid(self, form):
        """Create engine via API"""
        create_data = {
            'name': form.cleaned_data['name'],
            'description': form.cleaned_data['description'],
            'base_url': form.cleaned_data['base_url'],
            'admin_username': form.cleaned_data['admin_username'],
            'admin_password': form.cleaned_data['admin_password'],
            'is_active': form.cleaned_data['is_active'],
            'is_default': form.cleaned_data['is_default'],
            'engine_type': 'geoserver',  # Default type
        }
        
        data, error = self.get_api_data('/api/geodata/engines/', 'POST', json.dumps(create_data))
        
        if data:
            messages.success(self.request, 'Engine created successfully')
            return redirect(self.get_success_url())
        else:
            messages.error(self.request, f'Failed to create engine: {error}')
            return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'Add'
        context['object_type'] = 'Engine'
        return context

class EngineDeleteView(LoginRequiredMixin, TemplateView):
    """Delete engine in console"""
    template_name = 'console/engine_confirm_delete.html'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_client = Client()
    
    def get_api_data(self, endpoint):
        """Helper method to make API calls"""
        try:
            self.api_client.force_login(self.request.user)
            response = self.api_client.get(endpoint)
            
            if response.status_code == 200:
                return json.loads(response.content) if response.content else {}, None
            else:
                error_data = json.loads(response.content) if response.content else {}
                return None, error_data.get('detail', f'API Error: {response.status_code}')
        except Exception as e:
            logger.error(f"API call failed for {endpoint}: {e}")
            return None, f"API call failed: {str(e)}"
    
    def get_success_url(self):
        return reverse('console:engines')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Load engine data for confirmation
        engine_id = self.kwargs['pk']
        data, error = self.get_api_data(f'/api/geodata/engines/{engine_id}/')
        if data:
            context['object'] = data
            context['object_type'] = 'Engine'
        return context
    
    def post(self, request, *args, **kwargs):
        """Delete engine via API"""
        engine_id = self.kwargs['pk']
        
        try:
            self.api_client.force_login(request.user)
            response = self.api_client.delete(f'/api/geodata/engines/{engine_id}/')
            
            if response.status_code == 204:
                messages.success(request, 'Engine deleted successfully')
                return redirect(self.get_success_url())
            else:
                error_data = json.loads(response.content) if response.content else {}
                messages.error(request, f'Failed to delete engine: {error_data.get("detail", "Unknown error")}')
                return redirect(self.get_success_url())
        except Exception as e:
            logger.error(f"Engine delete failed: {e}")
            messages.error(request, f'Failed to delete engine: {str(e)}')
            return redirect(self.get_success_url())

# Workspace Edit Views
class WorkspaceEditView(BaseConsoleEditView):
    """Edit existing workspace in console"""
    template_name = 'console/workspace_form.html'
    form_class = WorkspaceForm
    
    def get_success_url(self):
        return reverse('console:workspaces')
    
    def get_initial(self):
        workspace_id = self.kwargs['pk']
        data, error = self.get_api_data(f'/api/geodata/workspaces/{workspace_id}/')
        if data:
            return {
                'name': data.get('name'),
                'description': data.get('description'),
                'is_active': data.get('is_active'),
            }
        return {}
    
    def form_valid(self, form):
        workspace_id = self.kwargs['pk']
        update_data = {
            'name': form.cleaned_data['name'],
            'description': form.cleaned_data['description'],
            'is_active': form.cleaned_data['is_active'],
        }
        
        data, error = self.get_api_data(f'/api/geodata/workspaces/{workspace_id}/', 'PATCH', json.dumps(update_data))
        
        if data:
            messages.success(self.request, 'Workspace updated successfully')
            return redirect(self.get_success_url())
        else:
            messages.error(self.request, f'Failed to update workspace: {error}')
            return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'Edit'
        context['object_type'] = 'Workspace'
        return context

class WorkspaceCreateView(BaseConsoleEditView):
    """Create new workspace in console"""
    template_name = 'console/workspace_form.html'
    form_class = WorkspaceForm
    
    def get_success_url(self):
        return reverse('console:workspaces')
    
    def form_valid(self, form):
        create_data = {
            'name': form.cleaned_data['name'],
            'description': form.cleaned_data['description'],
            'is_active': form.cleaned_data['is_active'],
        }
        
        data, error = self.get_api_data('/api/geodata/workspaces/', 'POST', json.dumps(create_data))
        
        if data:
            messages.success(self.request, 'Workspace created successfully')
            return redirect(self.get_success_url())
        else:
            messages.error(self.request, f'Failed to create workspace: {error}')
            return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'Add'
        context['object_type'] = 'Workspace'
        return context

class WorkspaceDeleteView(BaseConsoleEditView):
    """Delete workspace in console"""
    template_name = 'console/workspace_confirm_delete.html'
    
    def get_success_url(self):
        return reverse('console:workspaces')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        workspace_id = self.kwargs['pk']
        data, error = self.get_api_data(f'/api/geodata/workspaces/{workspace_id}/')
        if data:
            context['object'] = data
            context['object_type'] = 'Workspace'
        return context
    
    def post(self, request, *args, **kwargs):
        workspace_id = self.kwargs['pk']
        try:
            self.api_client.force_login(request.user)
            response = self.api_client.delete(f'/api/geodata/workspaces/{workspace_id}/')
            
            if response.status_code == 204:
                messages.success(request, 'Workspace deleted successfully')
                return redirect(self.get_success_url())
            else:
                error_data = json.loads(response.content) if response.content else {}
                messages.error(request, f'Failed to delete workspace: {error_data.get("detail", "Unknown error")}')
                return redirect(self.get_success_url())
        except Exception as e:
            logger.error(f"Workspace delete failed: {e}")
            messages.error(request, f'Failed to delete workspace: {str(e)}')
            return redirect(self.get_success_url())

# Store Edit Views  
class StoreEditView(BaseConsoleEditView):
    """Edit existing store in console"""
    template_name = 'console/store_form.html'
    form_class = StoreForm
    
    def get_success_url(self):
        return reverse('console:stores')
    
    def get_initial(self):
        store_id = self.kwargs['pk']
        data, error = self.get_api_data(f'/api/geodata/stores/{store_id}/')
        if data:
            return {
                'name': data.get('name'),
                'description': data.get('description'),
                'store_type': data.get('store_type'),
                'connection_params': json.dumps(data.get('connection_params', {}), indent=2) if data.get('connection_params') else '',
                'is_active': data.get('is_active'),
            }
        return {}
    
    def form_valid(self, form):
        store_id = self.kwargs['pk']
        connection_params = {}
        if form.cleaned_data['connection_params']:
            try:
                connection_params = json.loads(form.cleaned_data['connection_params'])
            except json.JSONDecodeError:
                form.add_error('connection_params', 'Invalid JSON format')
                return self.form_invalid(form)
        
        update_data = {
            'name': form.cleaned_data['name'],
            'description': form.cleaned_data['description'],
            'store_type': form.cleaned_data['store_type'],
            'connection_params': connection_params,
            'is_active': form.cleaned_data['is_active'],
        }
        
        data, error = self.get_api_data(f'/api/geodata/stores/{store_id}/', 'PATCH', json.dumps(update_data))
        
        if data:
            messages.success(self.request, 'Store updated successfully')
            return redirect(self.get_success_url())
        else:
            messages.error(self.request, f'Failed to update store: {error}')
            return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'Edit'
        context['object_type'] = 'Store'
        return context

class StoreCreateView(BaseConsoleEditView):
    """Create new store in console"""
    template_name = 'console/store_form.html'
    form_class = StoreForm
    
    def get_success_url(self):
        return reverse('console:stores')
    
    def form_valid(self, form):
        connection_params = {}
        if form.cleaned_data['connection_params']:
            try:
                connection_params = json.loads(form.cleaned_data['connection_params'])
            except json.JSONDecodeError:
                form.add_error('connection_params', 'Invalid JSON format')
                return self.form_invalid(form)
        
        create_data = {
            'name': form.cleaned_data['name'],
            'description': form.cleaned_data['description'],
            'store_type': form.cleaned_data['store_type'],
            'connection_params': connection_params,
            'is_active': form.cleaned_data['is_active'],
        }
        
        data, error = self.get_api_data('/api/geodata/stores/', 'POST', json.dumps(create_data))
        
        if data:
            messages.success(self.request, 'Store created successfully')
            return redirect(self.get_success_url())
        else:
            messages.error(self.request, f'Failed to create store: {error}')
            return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'Add'
        context['object_type'] = 'Store'
        return context

class StoreDeleteView(BaseConsoleEditView):
    """Delete store in console"""
    template_name = 'console/store_confirm_delete.html'
    
    def get_success_url(self):
        return reverse('console:stores')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        store_id = self.kwargs['pk']
        data, error = self.get_api_data(f'/api/geodata/stores/{store_id}/')
        if data:
            context['object'] = data
            context['object_type'] = 'Store'
        return context
    
    def post(self, request, *args, **kwargs):
        store_id = self.kwargs['pk']
        try:
            self.api_client.force_login(request.user)
            response = self.api_client.delete(f'/api/geodata/stores/{store_id}/')
            
            if response.status_code == 204:
                messages.success(request, 'Store deleted successfully')
                return redirect(self.get_success_url())
            else:
                error_data = json.loads(response.content) if response.content else {}
                messages.error(request, f'Failed to delete store: {error_data.get("detail", "Unknown error")}')
                return redirect(self.get_success_url())
        except Exception as e:
            logger.error(f"Store delete failed: {e}")
            messages.error(request, f'Failed to delete store: {str(e)}')
            return redirect(self.get_success_url())

# Layer Edit Views
class LayerEditView(BaseConsoleEditView):
    """Edit existing layer in console"""
    template_name = 'console/layer_form.html'
    form_class = LayerForm
    
    def get_success_url(self):
        return reverse('console:layers')
    
    def get_initial(self):
        layer_id = self.kwargs['pk']
        data, error = self.get_api_data(f'/api/geodata/layers/{layer_id}/')
        if data:
            return {
                'name': data.get('name'),
                'title': data.get('title'),
                'description': data.get('description'),
                'is_active': data.get('is_active'),
            }
        return {}
    
    def form_valid(self, form):
        layer_id = self.kwargs['pk']
        update_data = {
            'name': form.cleaned_data['name'],
            'title': form.cleaned_data['title'],
            'description': form.cleaned_data['description'],
            'is_active': form.cleaned_data['is_active'],
        }
        
        data, error = self.get_api_data(f'/api/geodata/layers/{layer_id}/', 'PATCH', json.dumps(update_data))
        
        if data:
            messages.success(self.request, 'Layer updated successfully')
            return redirect(self.get_success_url())
        else:
            messages.error(self.request, f'Failed to update layer: {error}')
            return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'Edit'
        context['object_type'] = 'Layer'
        return context

class LayerCreateView(BaseConsoleEditView):
    """Create new layer in console"""
    template_name = 'console/layer_form.html'
    form_class = LayerForm
    
    def get_success_url(self):
        return reverse('console:layers')
    
    def form_valid(self, form):
        create_data = {
            'name': form.cleaned_data['name'],
            'title': form.cleaned_data['title'],
            'description': form.cleaned_data['description'],
            'is_active': form.cleaned_data['is_active'],
        }
        
        data, error = self.get_api_data('/api/geodata/layers/', 'POST', json.dumps(create_data))
        
        if data:
            messages.success(self.request, 'Layer created successfully')
            return redirect(self.get_success_url())
        else:
            messages.error(self.request, f'Failed to create layer: {error}')
            return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'Add'
        context['object_type'] = 'Layer'
        return context

class LayerDeleteView(BaseConsoleEditView):
    """Delete layer in console"""
    template_name = 'console/layer_confirm_delete.html'
    
    def get_success_url(self):
        return reverse('console:layers')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        layer_id = self.kwargs['pk']
        data, error = self.get_api_data(f'/api/geodata/layers/{layer_id}/')
        if data:
            context['object'] = data
            context['object_type'] = 'Layer'
        return context
    
    def post(self, request, *args, **kwargs):
        layer_id = self.kwargs['pk']
        try:
            self.api_client.force_login(request.user)
            response = self.api_client.delete(f'/api/geodata/layers/{layer_id}/')
            
            if response.status_code == 204:
                messages.success(request, 'Layer deleted successfully')
                return redirect(self.get_success_url())
            else:
                error_data = json.loads(response.content) if response.content else {}
                messages.error(request, f'Failed to delete layer: {error_data.get("detail", "Unknown error")}')
                return redirect(self.get_success_url())
        except Exception as e:
            logger.error(f"Layer delete failed: {e}")
            messages.error(request, f'Failed to delete layer: {str(e)}')
            return redirect(self.get_success_url())
