from rest_framework import permissions, viewsets
from rest_framework.pagination import CursorPagination

from .models import GeoStory
from .serializers import GeoStorySerializer


class GeoStoryCursorPagination(CursorPagination):
    """Cursor pagination for GeoStory list."""
    page_size = 20
    ordering = "-created_at"


class GeoStoryViewSet(viewsets.ModelViewSet):
    """
    API endpoint for GeoStory CRUD operations.
    
    Supports filtering by campaign_id query parameter.
    """
    queryset = GeoStory.objects.all()
    serializer_class = GeoStorySerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = GeoStoryCursorPagination

    def get_queryset(self):
        """Filter by campaign_id if provided."""
        queryset = super().get_queryset()
        campaign_id = self.request.query_params.get("campaign_id")
        if campaign_id:
            queryset = queryset.filter(campaign_id=campaign_id)
        return queryset

    def perform_create(self, serializer):
        """Set the author to the current user."""
        serializer.save(author=self.request.user)
