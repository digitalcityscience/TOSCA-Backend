from rest_framework import permissions, viewsets
from rest_framework.pagination import CursorPagination

from .models import Campaign
from .serializers import (
    CampaignDetailSerializer,
    CampaignListSerializer,
    CampaignWriteSerializer,
)


class StandardCursorPagination(CursorPagination):
    """
    Standard cursor pagination for avoiding offset scanning.
    """
    page_size = 20
    ordering = "-created_at"


class CampaignViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows campaigns to be viewed or edited.
    """
    queryset = Campaign.objects.all()
    serializer_class = CampaignDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardCursorPagination

    def get_serializer_class(self):
        if self.action == "list":
            return CampaignListSerializer
        if self.action in ["create", "update", "partial_update"]:
            return CampaignWriteSerializer
        return CampaignDetailSerializer

    def perform_create(self, serializer):
        """Set the creator to the current user."""
        serializer.save(created_by=self.request.user)
