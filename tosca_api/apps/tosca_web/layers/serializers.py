from rest_framework import serializers

from .models import Layer


class LayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Layer
        fields = [
            "id",
            "name",
            "description",
            "owner",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "owner", "created_at", "updated_at"]
