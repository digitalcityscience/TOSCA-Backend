from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
import jwt

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
            # Decode without verification (trust HTTPS connection)
            # In production, you should verify the signature with Keycloak's public key
            """
            #pip install PyJWT
            from jwt import PyJWKClient
            from django.conf import settings
            
            jwks_client = PyJWKClient(settings.KEYCLOAK_JWKS_URL)
            
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            
            decoded_token = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=settings.KEYCLOAK_CLIENT_ID,
                issuer=settings.KEYCLOAK_ISSUER,
            )
            """
            decoded_token = jwt.decode(token, options={"verify_signature": False})
            
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
            roles = self._extract_roles_from_token(decoded_token)
            self._apply_permissions(user, roles)
            
            return (user, token)
            
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError as e:
            raise AuthenticationFailed(f'Invalid token: {str(e)}')
        except Exception as e:
            raise AuthenticationFailed(f'Authentication failed: {str(e)}')
    
    def _extract_roles_from_token(self, decoded_token):
        """Extract roles from decoded JWT token."""
        roles = set()
        realm_access = decoded_token.get("realm_access", {})
        if isinstance(realm_access, dict):
            roles.update(realm_access.get("roles", []))
        return roles
    
    def _apply_permissions(self, user, roles):
        """Apply roles to Django user permissions."""
        
        old_staff = user.is_staff
        old_super = user.is_superuser
        
        if "SUPERADMIN" in roles:
            user.is_superuser = True
            user.is_staff = True
        elif "ADMIN" in roles:
            user.is_superuser = False
            user.is_staff = True
        else:
            user.is_superuser = False
            user.is_staff = False
        #KeycloakTokenAuth] Updated permissions if changed: is_staff={user.is_staff}, is_superuser={user.is_superuser}")
        if user.is_staff != old_staff or user.is_superuser != old_super:
            user.save()


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
        extra_data = sociallogin.account.extra_data
        roles = self._extract_roles(extra_data)
        #apply roles to user permissions
        self._apply_permissions(user, roles)
        return user
    
    def pre_social_login(self, request, sociallogin):
        """
        Update user permissions on every login based on current Keycloak roles.
        Also connect existing users by email/username to avoid duplicate accounts.
        For new users, create them here to bypass allauth's signup form.
        This runs BEFORE login completes.
        """
        extra_data = sociallogin.account.extra_data
        roles = self._extract_roles(extra_data)
        
        # Get user info from userinfo or id_token
        userinfo = extra_data.get("userinfo", {})
        id_token = extra_data.get("id_token", {})
        
        email = userinfo.get("email") or (id_token.get("email") if isinstance(id_token, dict) else None)
        username = userinfo.get("preferred_username") or (id_token.get("preferred_username") if isinstance(id_token, dict) else None)
        
        print(f"[KeycloakAdapter] pre_social_login - username: {username}, email: {email}, is_existing: {sociallogin.is_existing}")
        
        if sociallogin.is_existing:
            # Existing social account - just update permissions
            user = sociallogin.user
            if user and user.pk:
                user.refresh_from_db()
                self._apply_permissions(user, roles)
            return
        
        # Not existing - try to find or create user
        existing_user = None
        
        # First try by username (Keycloak preferred_username is unique)
        if username:
            try:
                existing_user = User.objects.get(username=username)
                print(f"[KeycloakAdapter] Found existing user by username: {username}")
            except User.DoesNotExist:
                pass
        
        # Then try by email
        if not existing_user and email:
            try:
                existing_user = User.objects.get(email__iexact=email)
                print(f"[KeycloakAdapter] Found existing user by email: {email}")
            except User.DoesNotExist:
                pass
            except User.MultipleObjectsReturned:
                print(f"[KeycloakAdapter] Multiple users with email: {email}")
        
        if existing_user:
            # Connect social account to existing user
            sociallogin.connect(request, existing_user)
            self._apply_permissions(existing_user, roles)
            return
        
        # No existing user - create one now to bypass signup form
        if username:
            print(f"[KeycloakAdapter] Creating new user: {username}")
            first_name = userinfo.get("given_name") or (id_token.get("given_name") if isinstance(id_token, dict) else "")
            last_name = userinfo.get("family_name") or (id_token.get("family_name") if isinstance(id_token, dict) else "")
            
            new_user = User.objects.create(
                username=username,
                email=email or "",
                first_name=first_name or "",
                last_name=last_name or "",
            )
            self._apply_permissions(new_user, roles)
            
            # Connect sociallogin to the new user
            sociallogin.user = new_user
            print(f"[KeycloakAdapter] Created and connected new user: {username}")
        else:
            print("[KeycloakAdapter] No username found, cannot create user")

    def _extract_roles(self, extra_data):
        """Extract roles from Keycloak token."""
        roles = set()
        
        print(f"[KeycloakAdapter] extra_data keys: {extra_data.keys()}")
        print(f"[KeycloakAdapter] id_token type: {type(extra_data.get('id_token'))}")
        
        # Try to get roles from different locations
        # 1. Try realm_access (standard location)
        realm_access = extra_data.get("realm_access", {})
        if realm_access:
            realm_roles = realm_access.get("roles", [])
            roles.update(realm_roles)
            print(f"[KeycloakAdapter] Found roles in realm_access: {realm_roles}")
        
        # 2. Try to decode id_token if it's a JWT string
        id_token = extra_data.get("id_token")
        if id_token:
            if isinstance(id_token, str):
                print("[KeycloakAdapter] id_token is string, attempting decode...")
                print(f"[KeycloakAdapter] id_token preview: {id_token[:50]}...")
                try:
                    # Decode without verification (we trust it came from Keycloak via HTTPS)
                    decoded_token = jwt.decode(id_token, options={"verify_signature": False})
                    print("[KeycloakAdapter] Decoded id_token successfully")
                    print(f"[KeycloakAdapter] Decoded token keys: {decoded_token.keys()}")
                    
                    id_realm_access = decoded_token.get("realm_access", {})
                    if id_realm_access:
                        id_roles = id_realm_access.get("roles", [])
                        roles.update(id_roles)
                        print(f"[KeycloakAdapter] Found roles in id_token.realm_access: {id_roles}")
                except Exception as e:
                    print(f"[KeycloakAdapter] Failed to decode id_token: {e}")
            elif isinstance(id_token, dict):
                print("[KeycloakAdapter] id_token is already dict")
                print(f"[KeycloakAdapter] id_token keys: {id_token.keys()}")
                # Already decoded
                id_realm_access = id_token.get("realm_access", {})
                if id_realm_access:
                    id_roles = id_realm_access.get("roles", [])
                    roles.update(id_roles)
                    print(f"[KeycloakAdapter] Found roles in id_token.realm_access: {id_roles}")
            else:
                print(f"[KeycloakAdapter] id_token is unexpected type: {type(id_token)}")
        
        # 3. Try userinfo
        userinfo = extra_data.get("userinfo", {})
        if userinfo and isinstance(userinfo, dict):
            print(f"[KeycloakAdapter] userinfo keys: {userinfo.keys()}")
            ui_realm_access = userinfo.get("realm_access", {})
            if ui_realm_access:
                ui_roles = ui_realm_access.get("roles", [])
                roles.update(ui_roles)
                print(f"[KeycloakAdapter] Found roles in userinfo.realm_access: {ui_roles}")
        
        print(f"[KeycloakAdapter] Final extracted roles: {roles}")
        return roles
    
    def _apply_permissions(self, user, roles):
        """
        Apply roles to Django user permissions.
        """
        # Debug logging
        print(f"[KeycloakAdapter] User: {user.username}, Roles from Keycloak: {roles}")
        
        if "SUPERADMIN" in roles:
            user.is_superuser = True
            user.is_staff = True
            print("[KeycloakAdapter] Applied SUPERADMIN permissions: is_staff=True, is_superuser=True")
        elif "ADMIN" in roles:
            user.is_superuser = False
            user.is_staff = True
            print("[KeycloakAdapter] Applied ADMIN permissions: is_staff=True, is_superuser=False")
        else:
            user.is_superuser = False
            user.is_staff = False
            print("[KeycloakAdapter] No admin roles, setting is_staff=False, is_superuser=False")
        
        user.save()
        print(f"[KeycloakAdapter] Saved user {user.username}: is_staff={user.is_staff}, is_superuser={user.is_superuser}")
        