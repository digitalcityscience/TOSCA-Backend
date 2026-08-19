from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
import logging
from tosca_api.apps.core.jwt_utils import verify_and_decode_token
from tosca_api.apps.authentication.role_sync import (
    ExtractedOrg,
    ExtractedRoles,
    attach_auth_claims,
    build_auth_claims,
    extract_org_from_social_data,
    extract_org_from_token,
    extract_roles_from_social_data,
    extract_roles_from_token,
    run_org_login_checks,
    sync_user_permissions_from_roles,
)
from tosca_api.apps.authentication.role_registry import register_login_roles
from tosca_api.apps.organizations.policy import sync_snapshot
from tosca_api.apps.organizations.services import get_or_create_organization

logger = logging.getLogger(__name__)
User = get_user_model()


class NoSignupAccountAdapter(DefaultAccountAdapter):
    """
    Disable local signup - users must use Keycloak.
    Social (Keycloak) signup is still allowed.
    """
    def is_open_for_signup(self, request, sociallogin=None):
        """
        Disable local signup, but allow social (Keycloak) signup.
        """
        # Allow signup from Keycloak
        if sociallogin:
            return True
        # Deny local account signup
        return False
    
    def get_login_redirect_url(self, request):
        """Redirect admins to /admin/, normal users to a welcome page."""
        user = request.user
        if user.is_authenticated:
            if user.is_staff:
                return "/admin/"
        return "/welcome/"


class KeycloakTokenAuthentication(BaseAuthentication):
    """
    DRF authentication backend for Keycloak Bearer tokens.
    Validates JWT tokens and syncs roles to Django user permissions.
    For API token authentication from Mobile/Vue/Postman clients.
    """
    
    def authenticate(self, request):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None

        token = auth_header.split(' ')[1]

        try:
            decoded_token = verify_and_decode_token(token)
            username = decoded_token.get('preferred_username')
            if not username:
                raise AuthenticationFailed('Token does not contain username')

            # Get or create user
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': decoded_token.get('email', ''),
                    'first_name': decoded_token.get('given_name', ''),
                    'last_name': decoded_token.get('family_name', ''),
                }
            )

            # Sync roles from token
            roles = extract_roles_from_token(decoded_token)
            org = extract_org_from_token(decoded_token)
            self._apply_permissions(user, roles, org)

            # Non-blocking org coherence checks (log-only for API; no messages
            # framework on a Bearer request). See canonical §5d.
            if org.present and org.default_slug:
                get_or_create_organization(org.default_slug)
            run_org_login_checks(user, roles, org, request=None)

            # return decoded token as request.auth for downstream use
            return (user, decoded_token)
        except AuthenticationFailed:
            raise
        except Exception as e:
            raise AuthenticationFailed(f'Authentication failed: {str(e)}')

    def _apply_permissions(self, user, roles: ExtractedRoles, org: ExtractedOrg):
        """Apply roles to Django user permissions and attach request-local
        authorization claims (security tickets ticket 05).

        Bearer/API is always the freshest claims source and must never
        mutate the persisted ``UserAuthorizationSnapshot`` (Q11) -- only
        ``KeycloakAdapter`` (browser/admin login) does that.
        """
        sync_user_permissions_from_roles(user, roles)
        attach_auth_claims(user, build_auth_claims(roles, org))


class KeycloakAdapter(DefaultSocialAccountAdapter):
    """
    Convert Keycloak user data to Django User model.
    For Browser logins via allauth.
    Also sync roles to Django user permissions.
    """
    
    def is_auto_signup_allowed(self, request, sociallogin):
        """
        Always allow auto signup for Keycloak users.
        This bypasses the socialaccount/signup.html form.
        """
        return True
    
    def get_login_redirect_url(self, request):
        """Redirect admins to /admin/, normal users to welcome page after Keycloak login."""
        user = request.user
        if user.is_authenticated and user.is_staff:
            return "/admin/"
        return "/welcome/"
    
    def get_connect_redirect_url(self, request, socialaccount):
        """Redirect after connecting social account."""
        return self.get_login_redirect_url(request)
    
    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        user.email = data.get("email", "")
        user.first_name = data.get("given_name", "")
        user.last_name = data.get("family_name", "")
        return user
    
    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        #extract roles from Keycloak token
        roles = self._extract_roles(sociallogin)
        org = self._extract_org(sociallogin)
        #apply roles to user permissions
        self._apply_permissions(user, roles, org)
        return user

    def pre_social_login(self, request, sociallogin):
        """
        Update user permissions on every login based on current Keycloak roles.
        Also connect existing users by email/username to avoid duplicate accounts.
        For new users, create them here to bypass allauth's signup form.
        This runs BEFORE login completes.
        """
        extra_data = sociallogin.account.extra_data
        roles = self._extract_roles(sociallogin)
        org = self._extract_org(sociallogin)

        # Get user info from userinfo or id_token
        userinfo = extra_data.get("userinfo", {})
        id_token = extra_data.get("id_token", {})
        
        email = userinfo.get("email") or (id_token.get("email") if isinstance(id_token, dict) else None)
        username = userinfo.get("preferred_username") or (id_token.get("preferred_username") if isinstance(id_token, dict) else None)

        logger.info("Processing social login", extra={
            'username': username,
            'email': email,
            'is_existing': sociallogin.is_existing,
            'provider': sociallogin.account.provider
        })
        
        if sociallogin.is_existing:
            # Existing social account - just update permissions
            user = sociallogin.user
            if user and user.pk:
                user.refresh_from_db()
                self._apply_permissions(user, roles, org)
                self._run_login_checks(request, user, roles, org)
            return
        
        # Not existing - try to find or create user
        existing_user = None
        
        # First try by username (Keycloak preferred_username is unique)
        if username:
            try:
                existing_user = User.objects.get(username=username)
                logger.info("Connected existing user by username", extra={
                    'user_id': existing_user.id,
                    'username': username,
                    'connection_method': 'username_match'
                })
            except User.DoesNotExist:
                pass
        
        # Then try by email
        if not existing_user and email:
            try:
                existing_user = User.objects.get(email__iexact=email)
                logger.info("Connected existing user by email", extra={
                    'user_id': existing_user.id,
                    'username': existing_user.username,
                    'email': email,
                    'connection_method': 'email_match'
                })
            except User.DoesNotExist:
                pass
            except User.MultipleObjectsReturned:
                logger.error("Email conflict detected during login", extra={
                    'email': email,
                    'keycloak_username': username,
                    'action': 'auto_link_blocked',
                    'security_risk': True
                })
        
        if existing_user:
            # Connect social account to existing user
            sociallogin.connect(request, existing_user)
            self._apply_permissions(existing_user, roles, org)
            self._run_login_checks(request, existing_user, roles, org)
            return
        
        # No existing user - create one now to bypass signup form
        if username:
            first_name = userinfo.get("given_name") or (id_token.get("given_name") if isinstance(id_token, dict) else "")
            last_name = userinfo.get("family_name") or (id_token.get("family_name") if isinstance(id_token, dict) else "")
            
            new_user = User.objects.create(
                username=username,
                email=email or "",
                first_name=first_name or "",
                last_name=last_name or "",
            )
            self._apply_permissions(new_user, roles, org)
            self._run_login_checks(request, new_user, roles, org)

            # Connect sociallogin to the new user
            sociallogin.user = new_user
            logger.info("Created new user from social login", extra={
                'user_id': new_user.id,
                'username': username,
                'email': email,
                'provider': sociallogin.account.provider
            })
        else:
            logger.warning("No username found, cannot create user", extra={
                'email': email,
                'provider': sociallogin.account.provider
            })

    def _extract_roles(self, sociallogin):
        """Extract roles from the Keycloak login.

        Checks the access token first -- allauth's openid_connect provider
        never puts it in extra_data, but it's where Keycloak's default
        "roles" client scope actually lands realm_access.roles (ID
        token/userinfo mappers are a separate, often-off toggle).
        """
        extra_data = sociallogin.account.extra_data
        access_token = sociallogin.token.token if sociallogin.token else None
        return extract_roles_from_social_data(extra_data, access_token=access_token)

    def _extract_org(self, sociallogin):
        """Extract the default-organization claim from the Keycloak login.

        Same payload-source shape as ``_extract_roles`` (extra_data +
        access token), extracted once per login and threaded through to
        both ``_apply_permissions`` and ``_run_login_checks`` rather than
        re-parsed by each.
        """
        extra_data = sociallogin.account.extra_data
        access_token = sociallogin.token.token if sociallogin.token else None
        return extract_org_from_social_data(extra_data, access_token=access_token)

    def _apply_permissions(self, user, roles: ExtractedRoles, org: ExtractedOrg):
        """Apply roles to Django user permissions, attach request-local
        authorization claims, and persist them as this user's
        ``UserAuthorizationSnapshot`` (security tickets ticket 05).

        Browser/admin is the one path that writes the snapshot -- later
        requests in this session (and future sessions, until next login)
        read it back via ``organizations.policy.user_claims``. The write
        itself respects the authoritative rule (see ``policy.sync_snapshot``):
        a login with missing/non-authoritative role claims never overwrites
        a previously valid snapshot.
        """
        sync_user_permissions_from_roles(user, roles)
        claims = build_auth_claims(roles, org)
        attach_auth_claims(user, claims)
        sync_snapshot(user, claims)

    def _run_login_checks(self, request, user, roles, org):
        """Run non-blocking org coherence checks and surface user-facing warnings."""
        if org.present and org.default_slug:
            get_or_create_organization(org.default_slug)
        # Opportunistically grow the KeycloakRole catalog from this login's roles
        # (best-effort; never blocks login). Runs after get_or_create_organization
        # so the login's own org is resolvable. See role_registry (Epic 11 §4).
        register_login_roles(roles)
        run_org_login_checks(user, roles, org, request=request)
        
