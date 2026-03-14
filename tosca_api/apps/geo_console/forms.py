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
