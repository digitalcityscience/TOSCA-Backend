from rest_framework import serializers
from ..models import GeodataEngine, Workspace, Store, Layer


class GeodataEngineSerializer(serializers.ModelSerializer):
    """Serializer for GeodataEngine model"""
    geoserver_url = serializers.ReadOnlyField()
    admin_password = serializers.CharField(write_only=True, required=False)
    
    class Meta:
        model = GeodataEngine
        fields = [
            'id', 'name', 'description', 'engine_type', 'base_url', 'geoserver_url',
            'admin_username', 'admin_password', 'is_active', 'is_default', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'geoserver_url']
        extra_kwargs = {
            'admin_password': {'write_only': True, 'required': False}
        }
    
    def update(self, instance, validated_data):
        """Custom update to handle password"""
        admin_password = validated_data.pop('admin_password', None)
        
        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        # Update password if provided
        if admin_password:
            instance.admin_password = admin_password
            
        instance.save()
        return instance


class WorkspaceSerializer(serializers.ModelSerializer):
    """Serializer for Workspace model"""
    
    class Meta:
        model = Workspace
        fields = [
            'id', 'geodata_engine', 'name', 'description', 
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class StoreSerializer(serializers.ModelSerializer):
    """Serializer for Store model"""
    
    class Meta:
        model = Store
        fields = [
            'id', 'geodata_engine', 'workspace', 'name', 'description', 
            'host', 'port', 'database', 'username', 'schema',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {'password': {'write_only': True}}  # Don't expose password


class LayerSerializer(serializers.ModelSerializer):
    """Serializer for Layer model"""
    
    class Meta:
        model = Layer
        fields = [
            'id', 'workspace', 'store', 'name', 'title', 'description', 
            'table_name', 'geometry_column', 'geometry_type', 'srid',
            'publishing_state', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']