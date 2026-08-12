from django.contrib import admin

from tosca_api.apps.organizations.models import Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "keycloak_org_id", "created_at")
    search_fields = ("name", "slug", "keycloak_org_id")
    list_filter = ("is_active",)
    readonly_fields = ("id", "created_at", "updated_at")

    # Organization mirrors a native Keycloak org (canonical §5): the only
    # legitimate source of a row is `get_or_create_organization` firing on a
    # login carrying a `default_organization` claim Django hasn't seen yet
    # (see organizations/services.py). A hand-created row here would have a
    # `slug` with no matching Keycloak org, so anyone in its `ROLE_<SLUG>_*`
    # convention role would resolve to a workspace/campaign namespace that
    # doesn't actually exist -- never let admin manufacture that.
    def has_add_permission(self, request):
        return False

    # Roles are convention-derived (canonical §4b), shown read-only -- never edited
    # here, because Keycloak owns role assignment.
    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        if obj is not None:
            # slug drives role names; changing it after creation desyncs Keycloak.
            fields.append("slug")
        return fields
