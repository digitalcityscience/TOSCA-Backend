from django.conf import settings
from django.contrib.auth import logout, login
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.contrib.auth import get_user_model
import logging
from tosca_api.apps.authentication.role_sync import (
    extract_roles_from_social_data,
    sync_user_permissions_from_roles,
)

logger = logging.getLogger(__name__)

User = get_user_model()



class KeycloakLogoutView(View):
    """
    Logout from Django and redirect to Keycloak logout.
    """
    def _perform_logout(self, request):
        """Common logout logic for both GET and POST."""
        # Logout from Django
        logout(request)
        
        # Build redirect URI using reverse URL
        login_path = reverse('account_login')  # allauth login URL name
        redirect_uri = request.build_absolute_uri(login_path)
        
        # Redirect to Keycloak logout with client_id and redirect
        keycloak_logout_url = (
            f"{settings.KEYCLOAK_SERVER_URL}realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/logout"
            f"?client_id={settings.KEYCLOAK_CLIENT_ID}"
            f"&post_logout_redirect_uri={redirect_uri}"
        )
        
        return redirect(keycloak_logout_url)
    
    def get(self, request):
        return self._perform_logout(request)
    
    def post(self, request):
        return self._perform_logout(request)
    
class KeycloakRedirectView(View):
    """Show nice loading page and redirect to Keycloak."""
    
    def get(self, request):
        return render(request, 'authentication/account/login.html')
    
    def post(self, request):
        return render(request, 'authentication/account/login.html')

def welcome_view(request):
    """
    Welcome page for non-admin users.
    If not authenticated, redirect to login.
    """
    if not request.user.is_authenticated:
        return redirect('account_login')
    
    return render(request, 'authentication/account/welcome.html')


class AutoSignupView(View):
    """
    Automatically complete social signup without showing a form.
    This bypasses allauth's signup form completely.
    """
    
    def get(self, request):
        return self._process_signup(request)
    
    def post(self, request):
        return self._process_signup(request)
    
    def _process_signup(self, request):
        from allauth.socialaccount.models import SocialLogin
        
        # Get pending sociallogin from session
        data = request.session.get("socialaccount_sociallogin")
        if not data:
            logger.warning("No pending sociallogin in session", extra={
                'action': 'manual_signup_blocked',
                'session_id': request.session.session_key
            })
            return redirect('account_login')
        
        try:
            sociallogin = SocialLogin.deserialize(data)
        except (ValueError, KeyError) as e:
            logger.error("Failed to deserialize sociallogin", extra={
                'error': str(e),
                'session_id': request.session.session_key
            })
            return redirect('account_login')
        
        # Get user data from sociallogin
        extra_data = sociallogin.account.extra_data
        userinfo = extra_data.get("userinfo", {})
        id_token = extra_data.get("id_token", {})
        userinfo_data = userinfo if isinstance(userinfo, dict) else {}
        id_token_data = id_token if isinstance(id_token, dict) else {}
        
        # Get user info - prefer userinfo, fallback to id_token
        username = userinfo_data.get("preferred_username") or id_token_data.get("preferred_username")
        email = userinfo_data.get("email") or id_token_data.get("email", "")
        first_name = userinfo_data.get("given_name") or id_token_data.get("given_name", "")
        last_name = userinfo_data.get("family_name") or id_token_data.get("family_name", "")
        sub = userinfo_data.get("sub") or id_token_data.get("sub")  # Keycloak unique ID
        
        if not username:
            logger.warning("No username found in sociallogin", extra={
                'email': email,
                'sub': sub,
                'action': 'user_creation_failed'
            })
            return redirect('account_login')
        
        # Get or create user
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email': email,
                'first_name': first_name,
                'last_name': last_name,
            }
        )
        
        if created:
            logger.info("Created new user from manual signup", extra={
                'user_id': user.id,
                'username': username,
                'email': email,
                'sub': sub
            })
        else:
            logger.info("Updated existing user from manual signup", extra={
                'user_id': user.id,
                'username': username,
                'email': email
            })
            # Update user info
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.save(update_fields=["email", "first_name", "last_name"])
        
        # Extract and apply roles
        roles = self._extract_roles(extra_data)
        self._apply_permissions(user, roles)
        
        # Connect social account to user
        sociallogin.user = user
        sociallogin.save(request)
        
        # Clear pending signup from session
        request.session.pop("socialaccount_sociallogin", None)
        
        # Login the user
        login(request, user, backend='allauth.account.auth_backends.AuthenticationBackend')
        
        logger.info("User logged in via manual signup", extra={
            'user_id': user.id,
            'username': user.username,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
            'redirect_target': '/admin/' if user.is_staff else '/welcome/'
        })
        
        # Redirect based on role
        if user.is_staff:
            return redirect('/admin/')
        return redirect('welcome')
    
    def _extract_roles(self, extra_data):
        """Extract roles from Keycloak token."""
        return extract_roles_from_social_data(extra_data)
    
    def _apply_permissions(self, user, roles):
        """Apply roles to Django user permissions."""
        sync_user_permissions_from_roles(user, roles)
