from rest_framework import permissions, viewsets
from rest_framework.pagination import CursorPagination

from .models import GeoStory
from .serializers import (
    GeoStoryDetailSerializer,
    GeoStoryListSerializer,
    GeoStorySerializer,
)


class GeoStoryCursorPagination(CursorPagination):
    """Cursor pagination for GeoStory list."""

    page_size = 20
    ordering = "-created_at"


class GeoStoryViewSet(viewsets.ModelViewSet):
    """
    API endpoint for GeoStory operations.

    - **List**: Returns published stories with slim payload.
    - **Retrieve**: Returns full story with nested context, layers, links.
    - **Create/Update/Delete**: Requires authentication.

    Supports filtering by `campaign_id` query parameter.
    """

    queryset = GeoStory.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = GeoStoryCursorPagination

    def get_serializer_class(self):
        """
        Return appropriate serializer based on action.
        - list: GeoStoryListSerializer (slim)
        - retrieve: GeoStoryDetailSerializer (full nested)
        - create/update/delete: GeoStorySerializer (write capable)
        """
        if self.action == "list":
            return GeoStoryListSerializer
        if self.action == "retrieve":
            return GeoStoryDetailSerializer
        return GeoStorySerializer

    def get_queryset(self):
        """
        Filter queryset based on action and parameters.

        - List view: Only published stories (unless user is staff).
        - Filter by campaign_id if provided.
        - Optimize queries with select_related/prefetch_related.
        """
        queryset = super().get_queryset()

        # Apply status filter for list action (public consumption)
        if self.action == "list":
            # Staff users can see all, public users see only published
            if not self.request.user.is_staff:
                queryset = queryset.filter(status=GeoStory.Status.PUBLISHED)

        # Filter by campaign_id if provided
        campaign_id = self.request.query_params.get("campaign_id")
        if campaign_id:
            queryset = queryset.filter(campaign_id=campaign_id)

        # Optimize queries for detail view
        if self.action == "retrieve":
            queryset = queryset.select_related("context", "campaign", "author")
            queryset = queryset.prefetch_related("geostorylayer_set__layer")

        # Optimize queries for list view
        if self.action == "list":
            queryset = queryset.select_related("campaign")

        return queryset

    def perform_create(self, serializer):
        """Set the author to the current user."""
        serializer.save(author=self.request.user)
