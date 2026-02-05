from rest_framework import serializers

from .models import GeoStory


class GeoStorySerializer(serializers.ModelSerializer):
    """
    Serializer for GeoStory model.
    Basic CRUD - no nested writes (context, layers) yet.
    """

    class Meta:
        model = GeoStory
        fields = [
            "id",
            "title",
            "summary",
            "status",
            "campaign",
            "author",
            "context",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "author", "context", "created_at", "updated_at"]
