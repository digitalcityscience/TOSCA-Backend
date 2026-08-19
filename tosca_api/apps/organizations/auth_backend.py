"""Dynamic Django permission backend (security tickets ticket 06).

Makes ``has_perm()`` meaningful for non-superusers by computing capability
dynamically on every call -- no per-user ``Permission``/``Group`` rows are
ever synced from Keycloak. This is gate **A ∩ B** (org role capability
intersected with the org's app entitlement); it deliberately has **no**
row/object dimension of its own -- that stays gate C, owned by
``organizations.permissions`` querysets/object-permission checks and (for
admin) ``OrgScopedAdminMixin``.

Additive only: registering this backend only makes ``has_perm()`` return
something other than unconditional ``False`` for non-superusers. Nothing in
this diff removes ``OrgScopedAdminMixin``'s own capability ladder or any DRF
permission class -- those keep enforcing gate A themselves, side by side
with this backend, until tickets 07-12 migrate call sites onto ``has_perm()``
one at a time.
"""

from __future__ import annotations

from django.contrib.auth.backends import BaseBackend

from .models import Organization
from .policy import LEVEL_ACTIONS, enabled_apps_for, role_controlled_models_for_app, user_claims

# Only these verbs are ever computed dynamically (security tickets ticket 06,
# Layer A). Custom permissions (e.g. `publish_*`, `manage_*`) are
# intentionally out of scope for this backend -- it never grants them, no
# matter what role/entitlement the caller holds. Derived from LEVEL_ACTIONS
# (never a second hand-maintained set) so a future verb added there can't
# silently drift out of sync with what this filter allows through.
_MANAGED_ACTIONS = frozenset().union(*LEVEL_ACTIONS.values())


class OrgRolePermissionBackend(BaseBackend):
    """Computes ``has_perm()`` from (org role ∩ app entitlement ∩
    role-controlled models) -- no per-user permission rows, ever.

    Not an authentication backend in the login sense: ``authenticate()``
    always returns ``None`` so it never competes with ``ModelBackend``/
    allauth for credential checks. Its only job is ``has_perm()``.
    """

    def authenticate(self, request, **kwargs):
        return None

    def _capability_context(self, user_obj) -> tuple[str | None, set[str]]:
        """Resolve ``(level, entitled_apps)`` for ``user_obj``'s default org.

        ``level`` is ``None`` if any gate fails (inactive, no claims, no
        default-org role, unresolvable/unentitled organization) -- the
        shared prefix both ``has_perm`` and ``get_user_permissions`` need
        before diverging into "check one permission" vs. "enumerate all of
        them".
        """
        if user_obj is None or not getattr(user_obj, "is_active", False):
            return None, set()

        org_roles, default_org = user_claims(user_obj)
        if not default_org:
            return None, set()

        level = org_roles.get(default_org)
        if level is None:
            return None, set()

        organization = Organization.objects.filter(slug=default_org).first()
        if organization is None:
            return None, set()

        return level, enabled_apps_for(organization)

    def has_perm(self, user_obj, perm, obj=None) -> bool:
        if user_obj is None or not getattr(user_obj, "is_active", False):
            return False
        # Django's PermissionsMixin.has_perm() already short-circuits True
        # for an active superuser before any backend is consulted, but this
        # backend can also be called directly (e.g. in tests) -- mirror that
        # rule here so it's correct standalone too.
        if getattr(user_obj, "is_superuser", False):
            return True

        try:
            app_label, codename = perm.split(".", 1)
        except ValueError:
            return False

        action, _sep, model_name = codename.partition("_")
        if action not in _MANAGED_ACTIONS or not model_name:
            return False

        if model_name not in role_controlled_models_for_app(app_label):
            return False

        level, entitled_apps = self._capability_context(user_obj)
        if level is None or app_label not in entitled_apps:
            return False

        return action in LEVEL_ACTIONS[level]

    def has_module_perms(self, user_obj, app_label) -> bool:
        """Gate admin sidebar/app_index visibility on the same role +
        entitlement context ``has_perm`` uses -- without this, Django's
        module-visibility check falls through to ``ModelBackend``, which
        only sees real ``Permission`` rows. This backend never creates
        those rows (by design, see module docstring), so admin apps a user
        is actually entitled to via their org role would otherwise stay
        invisible in the UI even though ``has_perm`` grants the underlying
        actions.
        """
        if user_obj is None or not getattr(user_obj, "is_active", False):
            return False
        if getattr(user_obj, "is_superuser", False):
            return True

        _level, entitled_apps = self._capability_context(user_obj)
        return app_label in entitled_apps

    def get_user_permissions(self, user_obj, obj=None) -> set[str]:
        """Enumerate every ``app_label.action_model`` codename ``user_obj``
        currently holds -- the set ``has_perm`` checks membership against,
        materialized so Django's ``get_all_permissions()``/
        ``get_user_permissions()`` aggregators (which call this method, not
        ``has_perm``) see the same grants. First real caller: ticket 07's
        admin effective-permissions panel.

        Superusers are deliberately excluded here -- ``ModelBackend`` (listed
        before this backend in ``AUTHENTICATION_BACKENDS``) already returns
        every ``Permission`` in the system for an active superuser, and
        Django unions results across backends, so re-deriving the same
        (smaller, role-controlled-only) subset here would be redundant.
        """
        if obj is not None:
            return set()
        if getattr(user_obj, "is_superuser", False):
            return set()

        level, entitled_apps = self._capability_context(user_obj)
        if level is None:
            return set()

        actions = LEVEL_ACTIONS[level]
        return {
            f"{app_label}.{action}_{model_name}"
            for app_label in entitled_apps
            for model_name in role_controlled_models_for_app(app_label)
            for action in actions
        }
