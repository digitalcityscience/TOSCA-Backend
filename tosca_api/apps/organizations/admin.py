from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.http import HttpResponseRedirect

from tosca_api.apps.organizations.models import (
    Organization,
    OrganizationAppEntitlement,
    UserAuthorizationSnapshot,
)
from tosca_api.apps.organizations.policy import enabled_apps_for, is_platform_exempt, user_claims

User = get_user_model()


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
    list_display = ("user", "default_org", "platform_exempt", "synced_at")
    search_fields = ("user__username", "user__email", "default_org")
    readonly_fields = (
        "user",
        "org_roles",
        "default_org",
        "platform_exempt",
        "synced_at",
        "created_at",
        "updated_at",
    )

    # Snapshots are only ever written by the login sync path (ticket 05) --
    # admin is read-only visibility into what a user's last login granted.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Default Django ``UserAdmin`` plus a read-only effective-authorization
    panel (security tickets ticket 07): current/default org, org role(s),
    platform-exemption status, entitled apps, computed effective
    permissions, and when these claims were last synced.

    Read-only by design -- Keycloak owns role assignment (canonical §4b);
    this panel exists so a support/admin user can see what a user's last
    login actually granted without decoding a token by hand or
    cross-referencing Keycloak directly. `is_staff`/`is_superuser`/
    `is_active`/`groups`/`user_permissions` remain editable via Django's own
    "Permissions" fieldset (unchanged from the default `UserAdmin`) -- see
    `policy.is_platform_exempt`'s docstring for why the effective-permissions
    panel below deliberately does not read from those toggles.
    """

    effective_authorization_fields = (
        "effective_default_org",
        "effective_org_roles",
        "effective_platform_exempt",
        "effective_entitled_apps",
        "effective_permissions",
        "effective_synced_at",
    )

    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Effective authorization (read-only)", {"fields": effective_authorization_fields}),
    )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.extend(self.effective_authorization_fields)
        return fields

    @admin.display(description="Default organization")
    def effective_default_org(self, obj):
        _org_roles, default_org = user_claims(obj)
        return default_org or "(none)"

    @admin.display(description="Org role(s)")
    def effective_org_roles(self, obj):
        org_roles, _default_org = user_claims(obj)
        if not org_roles:
            return "(none)"
        return ", ".join(f"{org}: {level}" for org, level in sorted(org_roles.items()))

    @admin.display(description="Platform exempt (DJANGO_SUPERADMIN)", boolean=True)
    def effective_platform_exempt(self, obj):
        return is_platform_exempt(obj)

    @admin.display(description="Entitled apps (default org)")
    def effective_entitled_apps(self, obj):
        _org_roles, default_org = user_claims(obj)
        if not default_org:
            return "(no default org)"
        organization = Organization.objects.filter(slug=default_org).first()
        if organization is None:
            return "(organization not found)"
        apps = enabled_apps_for(organization)
        return ", ".join(sorted(apps)) if apps else "(none)"

    @admin.display(description="Effective permissions (get_all_permissions)")
    def effective_permissions(self, obj):
        if obj.is_superuser:
            return "(superuser -- all permissions)"
        perms = sorted(obj.get_all_permissions())
        return ", ".join(perms) if perms else "(none)"

    @admin.display(description="Claims last synced at")
    def effective_synced_at(self, obj):
        snapshot = getattr(obj, "authorization_snapshot", None)
        return snapshot.synced_at if snapshot is not None else "(never synced)"
