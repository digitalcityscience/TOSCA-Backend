from rest_framework import permissions, viewsets

from .models import Layer
from .serializers import LayerSerializer


class LayerViewSet(viewsets.ModelViewSet):
    """CRUD API for geospatial layers."""

    queryset = Layer.objects.select_related("owner")
    serializer_class = LayerSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)
