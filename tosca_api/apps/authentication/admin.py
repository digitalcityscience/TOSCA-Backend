from django.contrib import admin

from tosca_api.apps.authentication.models import KeycloakRole


@admin.register(KeycloakRole)
class KeycloakRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "source", "is_active", "last_seen_at")
    search_fields = ("name",)
    list_filter = ("source", "is_active", "organization")
    readonly_fields = ("first_seen_at", "last_seen_at")

    # KeycloakRole is a catalog populated from Keycloak (login upsert + Admin API
    # sync), never authored by hand: a manufactured name would point at no real
    # Keycloak role. Editing is limited to the org link / active flag.
    def has_add_permission(self, request):
        return False
