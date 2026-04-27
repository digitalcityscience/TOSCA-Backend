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


class StoreDetailForm(forms.Form):
    """
    Partial-edit form for an existing Store — used on the store detail page.
    Does NOT change name / workspace / store_type (those are read-only on detail).
    Primary use: fill in DB credentials that Django never received when the store
    was synced from GeoServer (GeoServer never exposes passwords via its REST API).
    """

    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional description'}),
    )
    host = forms.CharField(
        max_length=255,
        required=False,
        label='DB Host',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. db or 127.0.0.1', 'autocomplete': 'off'}),
    )
    port = forms.CharField(
        required=False,
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
        widget=forms.TextInput(attrs={'placeholder': 'e.g. tosca', 'autocomplete': 'off'}),
    )
    username = forms.CharField(
        max_length=100,
        required=False,
        label='DB Username',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. tosca_gs', 'autocomplete': 'off'}),
    )
    password = forms.CharField(
        max_length=100,
        required=False,
        label='DB Password',
        help_text='Leave blank to keep the existing password unchanged.',
        widget=forms.PasswordInput(
            render_value=False,
            attrs={'placeholder': 'New password (blank = keep existing)'},
        ),
    )
    schema = forms.CharField(
        max_length=100,
        required=False,
        label='Schema',
        widget=forms.TextInput(attrs={'autocomplete': 'off'}),
        help_text='PostGIS schema name.',
    )

    def clean_port(self):
        port = self.cleaned_data.get('port')
        if not port:
            return None  # keep existing
        try:
            val = int(str(port).strip())
        except (ValueError, TypeError):
            raise forms.ValidationError('Port must be a number (e.g. 5432).')
        if not (1 <= val <= 65535):
            raise forms.ValidationError('Port must be between 1 and 65535.')
        return val


GEOMETRY_TYPE_CHOICES = [
    ('Point', 'Point'),
    ('LineString', 'LineString'),
    ('Polygon', 'Polygon'),
    ('MultiPoint', 'MultiPoint'),
    ('MultiLineString', 'MultiLineString'),
    ('MultiPolygon', 'MultiPolygon'),
    ('GeometryCollection', 'GeometryCollection'),
]


class LayerPublishForm(forms.Form):
    """
    Form for publishing a PostGIS table as a GeoServer layer.
    workspace_choices and store_choices are injected at instantiation time.

    Usage:
        workspace_choices = [(w['id'], w['name']) for w in client.list_workspaces(engine_id=...)]
        form = LayerPublishForm(request.POST, workspace_choices=workspace_choices)
    """

    workspace_id = forms.ChoiceField(
        choices=[],
        label='Workspace',
    )
    store_id = forms.ChoiceField(
        choices=[],
        label='Datastore',
    )
    table_name = forms.CharField(
        max_length=100,
        label='PostGIS Table',
        widget=forms.TextInput(attrs={
            'placeholder': 'Select a table above or type manually',
            'autocomplete': 'off',
        }),
    )
    layer_name = forms.CharField(
        max_length=100,
        label='Layer Name',
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. buildings',
            'autocomplete': 'off',
            'pattern': r'^[a-zA-Z][a-zA-Z0-9_\-]*$',
        }),
        help_text='GeoServer layer identifier. Alphanumeric, underscore, hyphen only.',
    )
    title = forms.CharField(
        max_length=200,
        required=False,
        label='Title',
        widget=forms.TextInput(attrs={'placeholder': 'Human-readable title (optional)'}),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional description'}),
    )
    geometry_column = forms.CharField(
        max_length=100,
        initial='geom',
        label='Geometry Column',
        widget=forms.TextInput(attrs={'autocomplete': 'off'}),
    )
    geometry_type = forms.ChoiceField(
        choices=GEOMETRY_TYPE_CHOICES,
        label='Geometry Type',
    )
    srid = forms.IntegerField(
        initial=4326,
        label='Source CRS (EPSG)',
        widget=forms.NumberInput(attrs={'min': 1, 'max': 999999}),
        help_text='EPSG code of the source data (e.g. 4326 for WGS-84)',
    )

    def __init__(
        self,
        *args,
        workspace_choices: list | None = None,
        store_choices: list | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if workspace_choices:
            self.fields['workspace_id'].choices = [('', '— select a workspace —')] + list(workspace_choices)
        if store_choices:
            self.fields['store_id'].choices = [('', '— select a store —')] + list(store_choices)

    def clean_layer_name(self):
        import re
        name = self.cleaned_data['layer_name'].strip()
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_\-]*$', name):
            raise forms.ValidationError(
                'Layer name must start with a letter and contain only letters, digits, underscores, or hyphens.'
            )
        return name

    def clean_srid(self):
        srid = self.cleaned_data.get('srid')
        if srid is None or srid < 1:
            raise forms.ValidationError('SRID must be a positive integer (e.g. 4326).')
        return srid


class LayerEditForm(forms.Form):
    """
    Edit form for an already-registered Layer.

    GeoServer-synced (if layer is PUBLISHED): title, description.
    Django-only: srid.
    """

    title = forms.CharField(
        max_length=200,
        required=False,
        label='Title',
        widget=forms.TextInput(attrs={
            'placeholder': 'Human-readable display name',
            'autocomplete': 'off',
        }),
        help_text='Synced to GeoServer for published layers.',
    )
    description = forms.CharField(
        required=False,
        label='Description / Abstract',
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Optional description (also set as GeoServer abstract for published layers)',
        }),
        help_text='Synced to GeoServer abstract field for published layers.',
    )
    srid = forms.IntegerField(
        required=False,
        label='SRID (EPSG)',
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. 4326',
            'autocomplete': 'off',
        }),
        help_text='Spatial reference system code (e.g. 4326 for WGS-84). Django-side only.',
    )
