from rest_framework import serializers

from .models import ParticipationForm


class ParticipationFormSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParticipationForm
        fields = [
            "id",
            "title",
            "description",
            "submitted_by",
            "payload",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "submitted_by", "created_at", "updated_at"]
