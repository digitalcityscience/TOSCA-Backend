from rest_framework import serializers

from ..models import GeodataEngine, Layer, Store, Workspace


class LayerSummaryWorkspaceSerializer(serializers.ModelSerializer):
    """Slim workspace shape embedded inside LayerSummarySerializer."""

    class Meta:
        model = Workspace
        fields = ["id", "name"]
        read_only_fields = fields


class LayerSummarySerializer(serializers.ModelSerializer):
    """
    Slim, public-safe Layer projection used by consumer apps (geostories,
    events, feedback) to expose canonical layer metadata in detail responses.

    Stays narrower than the full LayerSerializer on purpose: it does not
    include connection/store details, publishing errors, or style assignments.
    """

    workspace = LayerSummaryWorkspaceSerializer(read_only=True)

    class Meta:
        model = Layer
        fields = [
            "id",
            "name",
            "workspace",
            "geometry_type",
            "srid",
            "published_url",
            "is_public",
            "publishing_state",
            "sync_state",
        ]
        read_only_fields = fields


class LayerUUIDListField(serializers.ListField):
    """
    Write-side field that accepts a list of Layer UUIDs and resolves them
    to ``geodata_providers.Layer`` instances.

    Used by consumer apps (geostories, events, feedback) for nested layer
    writes. Reports clear 400 errors for unknown UUIDs and pushes
    public + published validation through the same shared helper used by
    the through-model ``clean()`` methods.
    """

    child = serializers.UUIDField()

    def to_internal_value(self, data):
        from tosca_api.apps.geodata_providers.validators import (
            validate_layer_is_public_and_published,
        )

        ids = super().to_internal_value(data)
        if not ids:
            return []

        layers_by_id = Layer.objects.in_bulk(ids)
        missing = [str(i) for i in ids if i not in layers_by_id]
        if missing:
            raise serializers.ValidationError(
                f"Unknown layer id(s): {', '.join(missing)}"
            )

        from django.core.exceptions import ValidationError as DjangoValidationError

        resolved: list[Layer] = []
        for layer_id in ids:
            layer = layers_by_id[layer_id]
            try:
                validate_layer_is_public_and_published(layer)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    exc.message_dict.get("layer", str(exc))
                )
            resolved.append(layer)
        return resolved


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
            'public_url',
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
        fields = [
            'id', 'geodata_engine', 'engine_name', 'name', 'description',
            'sync_state', 'last_sync_at', 'last_sync_error',
            'remote_identifier', 'remote_hash', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'engine_name', 'sync_state', 'last_sync_at',
            'last_sync_error', 'remote_identifier', 'remote_hash',
            'created_at', 'updated_at',
        ]


class StoreSerializer(serializers.ModelSerializer):
    """Serializer for Store model."""

    workspace_name = serializers.CharField(source='workspace.name', read_only=True)
    has_password = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Store
        fields = [
            'id',
            'geodata_engine',
            'workspace',
            'workspace_name',
            'name',
            'store_type',
            'description',
            'host',
            'port',
            'database',
            'username',
            'password',
            'has_password',
            'schema',
            'file_path',
            'charset',
            'sync_state',
            'last_sync_at',
            'last_sync_error',
            'remote_identifier',
            'remote_hash',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'workspace_name', 'has_password', 'sync_state',
            'last_sync_at', 'last_sync_error', 'remote_identifier',
            'remote_hash', 'created_at', 'updated_at',
        ]
        extra_kwargs = {'password': {'write_only': True}}

    def get_has_password(self, obj) -> bool:
        """True if a non-empty password is stored (decrypted check)."""
        return bool(obj.decrypted_password)


class LayerSerializer(serializers.ModelSerializer):
    """Serializer for Layer model."""

    workspace_name = serializers.SerializerMethodField()
    store_name = serializers.SerializerMethodField()

    def get_workspace_name(self, obj):
        return obj.workspace.name if obj.workspace_id else None

    def get_store_name(self, obj):
        return obj.store.name if obj.store_id else None

    class Meta:
        model = Layer
        fields = [
            'id',
            'workspace',
            'workspace_name',
            'store',
            'store_name',
            'name',
            'title',
            'description',
            'table_name',
            'geometry_column',
            'geometry_type',
            'srid',
            'is_public',
            'publishing_state',
            'sync_state',
            'last_sync_at',
            'last_sync_error',
            'remote_identifier',
            'remote_hash',
            'publishing_error',
            'published_url',
            'published_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'workspace_name', 'store_name', 'sync_state',
            'last_sync_at', 'last_sync_error', 'remote_identifier',
            'remote_hash', 'publishing_error', 'published_url',
            'published_at', 'created_at', 'updated_at',
        ]
