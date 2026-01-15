from rest_framework.permissions import BasePermission


class IsActive(BasePermission):
    """Allow access only to authenticated and active users."""

    def has_permission(self, request, view):  # type: ignore[override]
        return bool(request.user and request.user.is_authenticated and request.user.is_active)
