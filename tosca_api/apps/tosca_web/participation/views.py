from rest_framework import permissions, viewsets

from .models import ParticipationForm
from .serializers import ParticipationFormSerializer


class ParticipationFormViewSet(viewsets.ModelViewSet):
    queryset = ParticipationForm.objects.all()
    serializer_class = ParticipationFormSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(submitted_by=self.request.user)
