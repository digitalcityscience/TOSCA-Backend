"""
Admin-specific forms for geodata_providers.

PublishPostGISForm — used by publish_postgis_view (Phase 4.3)
"""
from django import forms

from .models import Layer, Store, Workspace


GEOMETRY_TYPE_CHOICES = [
    ('Point',             'Point'),
    ('LineString',        'LineString'),
    ('Polygon',           'Polygon'),
    ('MultiPoint',        'MultiPoint'),
    ('MultiLineString',   'MultiLineString'),
    ('MultiPolygon',      'MultiPolygon'),
    ('GeometryCollection','GeometryCollection'),
]


class PublishPostGISForm(forms.Form):
    workspace = forms.ModelChoiceField(
        queryset=Workspace.objects.select_related('geodata_engine').order_by('geodata_engine__name', 'name'),
        label='Workspace',
        help_text='Target workspace. Engine is inferred from this workspace.',
    )
    store = forms.ModelChoiceField(
        queryset=Store.objects.select_related('workspace').order_by('workspace__name', 'name'),
        label='Store',
        help_text='PostGIS store to publish from. Must belong to the selected workspace.',
    )
    table_name = forms.CharField(
        max_length=100,
        label='Table name',
        help_text='PostGIS table (or view) name to publish. Select from available geometry tables.',
        widget=forms.Select(),
    )
    layer_name = forms.CharField(
        max_length=100,
        label='Layer name',
        help_text='GeoServer featuretype identifier. Defaults to table name if left blank.',
        required=False,
    )
    title = forms.CharField(
        max_length=200,
        label='Title',
        required=False,
        help_text='Human-readable display title. Defaults to layer name.',
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Description',
        required=False,
    )
    geometry_column = forms.CharField(
        max_length=100,
        initial='geom',
        label='Geometry column',
        help_text="Name of the geometry column in the PostGIS table (usually 'geom' or 'geometry').",
    )
    geometry_type = forms.ChoiceField(
        choices=GEOMETRY_TYPE_CHOICES,
        initial='Point',
        label='Geometry type',
    )
    srid = forms.IntegerField(
        initial=4326,
        label='SRID',
        help_text='Spatial Reference Identifier (e.g. 4326 for WGS-84).',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set default for geometry_column and srid if not provided
        if not self.data.get('geometry_column'):
            self.fields['geometry_column'].initial = 'geom'
        if not self.data.get('srid'):
            self.fields['srid'].initial = 4326
