from rest_framework import serializers

from ..models import GeodataEngine, Layer, Store, Workspace


class GeodataEngineSerializer(serializers.ModelSerializer):
    """Serializer for GeodataEngine model."""

    engine_url = serializers.ReadOnlyField()
    geoserver_url = serializers.ReadOnlyField()
    admin_password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = GeodataEngine
        fields = [
            'id',
            'name',
            'description',
            'engine_type',
            'base_url',
            'engine_url',
            'geoserver_url',
            'admin_username',
            'admin_password',
            'is_active',
            'is_default',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'engine_url', 'geoserver_url']
    
    def update(self, instance, validated_data):
        password = validated_data.pop('admin_password', None)
        # Only update the password if it's provided
        if password: 
            instance.admin_password = password
        return super().update(instance, validated_data)

class WorkspaceSerializer(serializers.ModelSerializer):
    """Serializer for Workspace model."""

    engine_name = serializers.CharField(source='geodata_engine.name', read_only=True)

    class Meta:
        model = Workspace
        fields = ['id', 'geodata_engine', 'engine_name', 'name', 'description', 'created_at', 'updated_at']
        read_only_fields = ['id', 'engine_name', 'created_at', 'updated_at']


class StoreSerializer(serializers.ModelSerializer):
    """Serializer for Store model."""

    class Meta:
        model = Store
        fields = [
            'id',
            'geodata_engine',
            'workspace',
            'name',
            'store_type',
            'description',
            'host',
            'port',
            'database',
            'username',
            'password',
            'schema',
            'file_path',
            'charset',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {'password': {'write_only': True}}


class LayerSerializer(serializers.ModelSerializer):
    """Serializer for Layer model."""

    class Meta:
        model = Layer
        fields = [
            'id',
            'workspace',
            'store',
            'name',
            'title',
            'description',
            'table_name',
            'geometry_column',
            'geometry_type',
            'srid',
            'is_public',
            'publishing_state',
            'publishing_error',
            'published_url',
            'published_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'publishing_error', 'published_url', 'published_at', 'created_at', 'updated_at']
