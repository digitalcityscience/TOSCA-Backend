"""
Role-based permission classes for API endpoints.

These permissions work with Keycloak roles that are synced to Django
by KeycloakAdapter in backends.py during login.
"""
from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    """
    Allow only users with SUPERADMIN role.
    
    Usage:
        @permission_classes([IsSuperAdmin])
        def admin_only_view(request):
            ...
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_superuser


class IsAdmin(BasePermission):
    """
    Allow users with SUPERADMIN or ADMIN roles.
    
    Usage:
        @permission_classes([IsAdmin])
        def staff_view(request):
            ...
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_staff


class IsEditor(BasePermission):
    """
    Allow users in 'editor' Django group.
    
    Note: You need to map Keycloak roles to Django groups in backends.py
    
    Usage:
        @permission_classes([IsEditor])
        def edit_content(request):
            ...
    """
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated 
            and request.user.groups.filter(name='editor').exists()
        )


class IsViewer(BasePermission):
    """
    Allow users in 'viewer' Django group.
    
    Note: You need to map Keycloak roles to Django groups in backends.py
    
    Usage:
        @permission_classes([IsViewer])
        def view_content(request):
            ...
    """
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated 
            and request.user.groups.filter(name='viewer').exists()
        )