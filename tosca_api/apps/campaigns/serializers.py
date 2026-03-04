from rest_framework import serializers

from .models import Campaign


class CampaignListSerializer(serializers.ModelSerializer):
    """
    Slim serializer for Campaign list views.
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
        read_only_fields = fields


class CampaignDetailSerializer(serializers.ModelSerializer):
    """
    Detailed serializer for Campaign retrieval.
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
        read_only_fields = fields


class CampaignWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating and updating Campaigns.
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

    def validate(self, attrs):
        """Invoke model clean() for DB-level validation."""
        instance = Campaign(**attrs)
        if self.instance:
            # Update scenario: apply new attrs to a copy of existing instance
            for attr, value in attrs.items():
                setattr(instance, attr, value)
        
        instance.clean()
        return attrs
