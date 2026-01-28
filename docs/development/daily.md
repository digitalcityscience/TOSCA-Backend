---
27012026
---

Aşağıda yapılması gerekenleri kısa ve net bir checklist olarak, her madde için de ayrı ayrı kullanabileceğin prompt’lar ile verdim.
Dil sade, uygulamaya dönük ve copy-paste odaklıdır.

⸻

1. Implement JWT Signature Verification (CRITICAL)

What to do
• Verify JWT using RS256 via JWKS from Keycloak
• Enforce checks for: exp, iss, aud (fallback to azp)
• Centralize verification logic in one reusable function

Prompt

Implement a reusable `verify_and_decode_token(token: str)` function in Django that:

- Fetches public keys from KEYCLOAK_JWKS_URL using PyJWKClient
- Verifies RS256 signature
- Validates `exp`, `iss`, and `aud`
- If `aud` validation fails, checks `azp` as fallback
- Raises explicit authentication errors (401) on failure
  Return the decoded payload on success.

⸻

2. Make Token Verification the Single Source of Truth

What to do
• Use the same verification function in:
• KeycloakTokenAuthentication
• KeycloakAdapter (login / callback flow)
• Avoid duplicate decode / verify logic

Prompt

Refactor authentication code so that both

- DRF TokenAuthentication
- django-allauth Keycloak adapter
  use the same `verify_and_decode_token()` utility.
  Ensure no JWT is decoded without signature verification.

⸻

3. Add Required Security Settings

What to do
• Add mandatory env/config variables
• Document them clearly

Prompt

Add the following settings with validation:

- KEYCLOAK_JWKS_URL
- KEYCLOAK_ISSUER
- ALLOWED_TOKEN_AUDIENCES (list)

Update `.env.example` and Django settings to fail fast if any of them are missing.

⸻

4. Implement Role → Group → Permission Mapping

What to do
• Define a config-based mapping: Keycloak roles → Django Groups
• Sync groups on login
• Assign Django permissions to groups (not users)

Prompt

Implement a role-to-group synchronization mechanism:

- Define a mapping in Django settings (Keycloak role → Django group)
- On login, read roles from token claims
- Create missing Django groups automatically
- Assign users to groups based on token roles
- Remove groups if roles were removed in Keycloak

⸻

5. Use Django Permissions for Authorization

What to do
• Protect endpoints using has_perm() or DRF permission classes
• Avoid role-name checks inside views

Prompt

Refactor API authorization so that:

- Endpoint access is controlled via Django permissions
- Views and DRF permission classes rely on `user.has_perm()`
- No direct Keycloak role name checks exist in views

⸻

6. Secure Email Auto-Linking Logic

What to do
• Never auto-link if multiple users share the same email
• Store sub claim for stable user binding
• Add audit logging

Prompt

Improve pre_social_login logic:

- If multiple users exist with the same email, block auto-linking
- Log the conflict as a security/audit event
- Store the Keycloak `sub` claim in the user profile or SocialAccount
- Use `sub` as the primary identifier for future logins

⸻

7. Replace print() with Structured Logging

What to do
• Use Python logging with levels
• Never silently swallow security errors

Prompt

Replace all print() statements with structured logging:

- Use logger.info / warning / error
- Log JWT verification failures, email conflicts, role sync issues
- Ensure critical auth failures are visible in logs

⸻

8. Write Security-Focused Tests (Priority Order)

What to do
• Test token verification first
• Then adapter & role sync behavior

Prompt

Write pytest + pytest-django tests for:

1. JWT verification:
   - valid signed token → pass
   - invalid signature → 401
   - expired token → 401
   - wrong issuer or audience → 401
2. Keycloak adapter:
   - existing user matched by username
   - existing user matched by unique email
   - multiple users with same email → no auto-connect
3. Role sync:
   - role added → group added
   - role removed → group removed
     Use a test RS256 key or mock PyJWKClient.

⸻

9. Bootstrap Initial Groups & Permissions

What to do
• Ensure required groups exist in fresh environments
• Avoid manual admin setup

Prompt

Create a Django migration or management command that:

- Creates initial Django groups
- Assigns required permissions to each group
- Can be safely re-run without side effects

⸻
