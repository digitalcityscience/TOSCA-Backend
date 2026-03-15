from django import forms

ENGINE_TYPE_CHOICES = [
    ('geoserver', 'GeoServer'),
    ('martin', 'Martin Tiles'),
    ('pg_tileserv', 'PostGIS TileServer'),
]


class EngineForm(forms.Form):
    """
    Form for creating and editing a GeodataEngine via the internal DRF API.
    No model binding — data is POSTed to the API layer.
    """

    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Default GeoServer',
            'autocomplete': 'off',
        }),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Optional description',
        }),
    )
    engine_type = forms.ChoiceField(choices=ENGINE_TYPE_CHOICES)
    base_url = forms.CharField(
        max_length=255,
        label='Base URL',
        widget=forms.TextInput(attrs={
            'placeholder': 'http://geoserver:8080/geoserver',
            'autocomplete': 'off',
        }),
    )
    admin_username = forms.CharField(
        max_length=100,
        required=False,
        label='Admin Username',
        widget=forms.TextInput(attrs={
            'placeholder': 'admin',
            'autocomplete': 'off',
        }),
    )
    admin_password = forms.CharField(
        max_length=100,
        required=False,
        label='Admin Password',
        widget=forms.PasswordInput(
            render_value=False,
            attrs={'placeholder': 'Leave blank to keep existing password'},
        ),
    )
    is_active = forms.BooleanField(required=False, initial=True, label='Active')
    is_default = forms.BooleanField(required=False, initial=False, label='Default engine')


class WorkspaceForm(forms.Form):
    """
    Form for creating a Workspace via the internal DRF API.
    No model binding — data is POSTed to the API layer.

    engine_choices must be passed at instantiation time because the
    available engines are fetched from the API, not the ORM.

    Usage:
        choices = [(e['id'], e['name']) for e in client.list_engines()]
        form = WorkspaceForm(request.POST, engine_choices=choices)
    """

    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. my_workspace',
            'autocomplete': 'off',
            'pattern': r'^[a-zA-Z][a-zA-Z0-9_\-]*$',
        }),
        help_text='Letters, digits, underscores and hyphens only. Must start with a letter.',
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Optional description',
        }),
    )
    geodata_engine = forms.ChoiceField(
        label='Engine',
        choices=[('', '— select an engine —')],
    )

    def __init__(self, *args, engine_choices: list | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if engine_choices:
            self.fields['geodata_engine'].choices = [('', '— select an engine —')] + list(engine_choices)

    def clean_name(self):
        """Reject names that contain the workspace:name separator GeoServer uses."""
        name = self.cleaned_data['name']
        if ':' in name:
            raise forms.ValidationError(
                "Workspace name must not contain a colon — GeoServer uses 'workspace:name' as its wire format."
            )
        return name


STORE_TYPE_CHOICES = [
    ('postgis', 'PostGIS Database'),
    ('file', 'File-based Store (Shapefile / GeoPackage / GeoJSON)'),
    ('geotiff', 'GeoTIFF'),
]


class StoreForm(forms.Form):
    """
    Form for creating a Store via the internal DRF API.
    No model binding — data is POSTed to the API layer.

    workspace_choices must be passed at instantiation time because the
    available workspaces are fetched from the API, not the ORM.

    Usage:
        choices = [(ws['id'], ws['name']) for ws in client.list_workspaces()]
        form = StoreForm(request.POST, workspace_choices=choices)
    """

    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. postgis_vector',
            'autocomplete': 'off',
            'pattern': r'^[a-zA-Z][a-zA-Z0-9_\-]*$',
        }),
        help_text='Letters, digits, underscores and hyphens only. Must start with a letter.',
    )
    workspace = forms.ChoiceField(
        label='Workspace',
        choices=[('', '— select a workspace —')],
    )
    store_type = forms.ChoiceField(
        choices=STORE_TYPE_CHOICES,
        initial='postgis',
        label='Store Type',
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Optional description',
        }),
    )

    # PostGIS connection fields
    host = forms.CharField(
        max_length=255,
        required=False,
        label='DB Host',
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. postgis or 127.0.0.1',
            'autocomplete': 'off',
        }),
    )
    port = forms.CharField(
        required=False,
        initial='5432',
        label='Port',
        widget=forms.TextInput(attrs={
            'placeholder': '5432',
            'autocomplete': 'off',
            'inputmode': 'numeric',
            'style': 'max-width: 100px;',
        }),
    )
    database = forms.CharField(
        max_length=100,
        required=False,
        label='Database',
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. gis_db',
            'autocomplete': 'off',
        }),
    )
    username = forms.CharField(
        max_length=100,
        required=False,
        label='DB Username',
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. geoserver_user',
            'autocomplete': 'off',
        }),
    )
    password = forms.CharField(
        max_length=100,
        required=False,
        label='DB Password',
        widget=forms.PasswordInput(
            render_value=False,
            attrs={'placeholder': 'Database password'},
        ),
    )
    schema = forms.CharField(
        max_length=100,
        required=False,
        label='Schema',
        widget=forms.TextInput(attrs={'autocomplete': 'off'}),
        help_text='PostGIS schema name. Must already exist in the database.',
    )

    def __init__(self, *args, workspace_choices: list | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if workspace_choices:
            self.fields['workspace'].choices = [('', '— select a workspace —')] + list(workspace_choices)
        # Default schema from settings if not already bound
        if not self.data.get('schema') and not self.initial.get('schema'):
            from django.conf import settings
            self.fields['schema'].initial = getattr(settings, 'GIS_SCHEMA', 'public')

    def clean_name(self):
        name = self.cleaned_data['name']
        if ':' in name:
            raise forms.ValidationError("Store name must not contain a colon.")
        return name

    def clean_port(self):
        port = self.cleaned_data.get('port')
        if not port:
            return 5432
        try:
            val = int(str(port).strip())
        except (ValueError, TypeError):
            raise forms.ValidationError('Port must be a number (e.g. 5432).')
        if not (1 <= val <= 65535):
            raise forms.ValidationError('Port must be between 1 and 65535.')
        return val
