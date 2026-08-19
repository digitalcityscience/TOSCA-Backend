from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.http import HttpResponseRedirect

from tosca_api.apps.organizations.models import (
    Organization,
    OrganizationAppEntitlement,
    UserAuthorizationSnapshot,
)


class OrganizationAppEntitlementInline(admin.TabularInline):
    model = OrganizationAppEntitlement
    extra = 0
    readonly_fields = ("created_at", "updated_at")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "keycloak_org_id", "created_at")
    search_fields = ("name", "slug", "keycloak_org_id")
    list_filter = ("is_active",)
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [OrganizationAppEntitlementInline]

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

    # Deleting an org must first remove its mirrored reader/writer roles from the
    # GeoServer role service (see organizations/signals + role_sync). The
    # pre_delete signal already *blocks* the delete if GeoServer can't be reached,
    # but that raises inside delete_view's transaction -> a 500. So we run the same
    # cleanup here, *before* the delete transaction, and turn a GeoServer failure
    # into a friendly "delete cancelled" message instead. On success the delete
    # proceeds and the signal's re-run is an idempotent no-op (roles already gone).
    def delete_view(self, request, object_id, extra_context=None):
        if request.method == "POST":
            from tosca_api.apps.geodata_providers.role_sync import (
                GeoServerRoleCleanupError,
                mirror_org_role_deletion,
            )

            obj = self.get_object(request, unquote(object_id))
            if obj is not None:
                try:
                    mirror_org_role_deletion(obj)
                except GeoServerRoleCleanupError as exc:
                    self.message_user(
                        request,
                        "Silme iptal edildi — GeoServer rollerini temizleyemedim "
                        f"(bağlantı hatası olabilir): {exc}",
                        level=messages.ERROR,
                    )
                    return HttpResponseRedirect(request.path)
        return super().delete_view(request, object_id, extra_context)

    # Remove the bulk "delete selected" action: it deletes inside its own
    # transaction, so a GeoServer-blocked org there would 500 instead of showing
    # the friendly message above. Org deletion is rare + destructive -- one guarded
    # single-object path is enough.
    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


@admin.register(UserAuthorizationSnapshot)
class UserAuthorizationSnapshotAdmin(admin.ModelAdmin):
    list_display = ("user", "default_org", "synced_at")
    search_fields = ("user__username", "user__email", "default_org")
    readonly_fields = ("user", "org_roles", "default_org", "synced_at", "created_at", "updated_at")

    # Snapshots are only ever written by the login sync path (ticket 05) --
    # admin is read-only visibility into what a user's last login granted.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
