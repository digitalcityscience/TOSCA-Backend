from rest_framework import serializers

from .models import Campaign


class CampaignSerializer(serializers.ModelSerializer):
    """
    Serializer for Campaign model.
    """

    class Meta:
        model = Campaign
        fields = [
            "id",
            "title",
            "summary",
            "status",
            "visibility",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]
