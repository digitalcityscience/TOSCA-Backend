from rest_framework import permissions, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import CursorPagination

from tosca_api.apps.organizations.permissions import (
    OrgScopedPermission,
    ViewGatedModelPermissions,
    org_scoped_queryset,
    resolve_write_organization,
)

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
    permission_classes = [permissions.IsAuthenticated, ViewGatedModelPermissions, OrgScopedPermission]
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        return org_scoped_queryset(self.request, Campaign.objects.all())

    def get_serializer_class(self):
        if self.action == "list":
            return CampaignListSerializer
        if self.action in ["create", "update", "partial_update"]:
            return CampaignWriteSerializer
        return CampaignDetailSerializer

    def perform_create(self, serializer):
        """Set the creator and owning organization to the current user's."""
        organization = resolve_write_organization(self.request)
        if organization is None:
            raise ValidationError({"organization": "Could not determine an organization for this campaign."})
        serializer.save(created_by=self.request.user, organization=organization)
