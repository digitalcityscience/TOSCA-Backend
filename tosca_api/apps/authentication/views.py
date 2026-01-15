from django.conf import settings
from django.contrib.auth import logout, login
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.contrib.auth import get_user_model
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

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
        return render(request, 'account/login.html')
    
    def post(self, request):
        return render(request, 'account/login.html')

def welcome_view(request):
    """
    Welcome page for non-admin users.
    If not authenticated, redirect to login.
    """
    if not request.user.is_authenticated:
        return redirect('/accounts/login/')
    
    return render(request, 'account/welcome.html')


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
            print("[AutoSignupView] No pending sociallogin in session")
            return redirect('/accounts/login/')
        
        try:
            sociallogin = SocialLogin.deserialize(data)
        except (ValueError, KeyError) as e:
            print(f"[AutoSignupView] Failed to deserialize sociallogin: {e}")
            return redirect('/accounts/login/')
        
        # Get user data from sociallogin
        extra_data = sociallogin.account.extra_data
        userinfo = extra_data.get("userinfo", {})
        id_token = extra_data.get("id_token", {})
        
        # Get user info - prefer userinfo, fallback to id_token
        username = userinfo.get("preferred_username") or id_token.get("preferred_username")
        email = userinfo.get("email") or id_token.get("email", "")
        first_name = userinfo.get("given_name") or id_token.get("given_name", "")
        last_name = userinfo.get("family_name") or id_token.get("family_name", "")
        sub = userinfo.get("sub") or id_token.get("sub")  # Keycloak unique ID
        
        print(f"[AutoSignupView] Creating user: {username}, email: {email}, sub: {sub}")
        
        if not username:
            print("[AutoSignupView] No username found")
            return redirect('/accounts/login/')
        
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
            print(f"[AutoSignupView] Created new user: {username}")
        else:
            print(f"[AutoSignupView] Found existing user: {username}")
            # Update user info
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
        
        # Extract and apply roles
        roles = self._extract_roles(extra_data)
        self._apply_permissions(user, roles)
        user.save()
        
        # Connect social account to user
        sociallogin.user = user
        sociallogin.save(request)
        
        # Clear pending signup from session
        request.session.pop("socialaccount_sociallogin", None)
        
        # Login the user
        login(request, user, backend='allauth.account.auth_backends.AuthenticationBackend')
        
        print(f"[AutoSignupView] User logged in: {username}, is_staff: {user.is_staff}")
        
        # Redirect based on role
        if user.is_staff:
            return redirect('/admin/')
        return redirect('/welcome/')
    
    def _extract_roles(self, extra_data):
        """Extract roles from Keycloak token."""
        roles = set()
        id_token = extra_data.get("id_token", {})
        if isinstance(id_token, dict):
            realm_access = id_token.get("realm_access", {})
            if realm_access:
                roles.update(realm_access.get("roles", []))
        return roles
    
    def _apply_permissions(self, user, roles):
        """Apply roles to Django user permissions."""
        print(f"[AutoSignupView] Applying roles: {roles}")
        if "SUPERADMIN" in roles:
            user.is_superuser = True
            user.is_staff = True
        elif "ADMIN" in roles:
            user.is_superuser = False
            user.is_staff = True
        else:
            user.is_superuser = False
            user.is_staff = False


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def test_token_auth(request):
    """Test endpoint to validate token authentication."""
    return Response({
        'message': 'Token authentication successful!',
        'user': request.user.username,
        'email': request.user.email,
        'is_staff': request.user.is_staff,
        'is_superuser': request.user.is_superuser,
        'auth_method': str(request.auth.__class__.__name__) if request.auth else 'session'
    })
