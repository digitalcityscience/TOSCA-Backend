# TOSCA-Web API - AI Coding Agent Instructions

## Project Overview
Django 5.1 REST API backend for a geospatial web platform. Uses Keycloak for authentication, GeoServer for serving map layers, and PostGIS for geospatial data storage.

## Architecture

### Three-Service Stack
- **Django** (this repo): Control plane for access control decisions, user management, and API orchestration
- **Keycloak**: IAM provider - stores users, roles, issues JWT tokens
- **GeoServer**: Geospatial server accessed directly by clients using JWT authentication (no Django proxy)

### Critical Design Decision: Direct GeoServer Access
Clients access GeoServer directly for WMS/WFS/WMTS requests. Django does NOT proxy these requests. This architectural choice means:
- All access control must be **role-based (RBAC)**, not attribute-based (ABAC)
- Access rules are **precomputed** and synchronized to GeoServer
- No per-request authorization logic is possible for GeoServer endpoints

See [docs/development/IAM_access_control.md](../docs/development/IAM_access_control.md) for the complete access control model.

### App Structure
```
tosca_api/
├── settings/          # Environment-based: base.py, development.py, production.py, test.py
├── apps/
│   ├── core/         # Shared utilities (TimeStampedModel, pagination, permissions, jwt_utils)
│   ├── authentication/  # Keycloak OAuth2/OIDC integration (django-allauth)
│   └── tosca_web/    # Main API endpoints (layers, participation)
```

## Environment & Settings

### Settings Module Selection
Use `ENV` variable to select environment file:
- `ENV=dev` → uses `.env.dev` → loads `tosca_api.settings.development`
- `ENV=prod` → uses `.env.prod` → loads `tosca_api.settings.production`
- Tests always use `tosca_api.settings.test` (configured in pytest.ini)

### Database Schema Architecture
PostGIS database with 3 schemas:
- `tosca_api`: Django models and application data (Django connects here)
- `geoserver`: GeoServer workspace/layer metadata
- `gis`: Actual geospatial data (geometry tables)

Django's `search_path` is set in [tosca_api/settings/base.py](../tosca_api/settings/base.py#L120) via `PG_SCHEMA_API` env var.

## Authentication & Authorization

### Keycloak Integration Flow
1. User clicks login → redirected to Keycloak
2. After authentication, Keycloak redirects back with authorization code
3. `KeycloakAdapter.pre_social_login()` in [tosca_api/apps/authentication/backends.py](../tosca_api/apps/authentication/backends.py) handles:
   - User creation/lookup by username
   - Role synchronization from JWT token claims
   - Django permission assignment (is_staff, is_superuser based on roles)

See [tosca_api/apps/authentication/docs/keycloak_logic.md](../tosca_api/apps/authentication/docs/keycloak_logic.md) for sequence diagrams.

### API Authentication
DRF uses `KeycloakTokenAuthentication` for Bearer token validation:
- Verifies JWT signature using Keycloak's JWKS endpoint
- Extracts roles from `realm_access.roles` and `resource_access.{client}.roles`
- Syncs roles to Django user on every request
- Available for mobile/Vue/API clients

Default auth classes in [tosca_api/settings/base.py](../tosca_api/settings/base.py#L153):
```python
"DEFAULT_AUTHENTICATION_CLASSES": [
    "tosca_api.apps.authentication.backends.KeycloakTokenAuthentication",  # JWT
    "rest_framework.authentication.SessionAuthentication",  # Browser
]
```

### Role Mapping
- `ROLE_GEOSERVER_ADMIN` → `is_staff=True`, `is_superuser=True`
- `ROLE_AUTHENTICATED` → Any logged-in user
- `ROLE_PUBLIC_ACCESS` → Anonymous users (mapped to GeoServer's `ROLE_ANONYMOUS`)

## Development Workflows

### Package Management (UV)
Uses `uv` for Python dependency management (not pip):
```bash
make uv-sync              # Install dependencies from lockfile
make uv-add PKG=requests  # Add new package
make uv-lock              # Update lockfile
```

Never commit changes to `pyproject.toml` without running `make uv-lock` to update `uv.lock`.

### Docker Development
Primary workflow uses Docker Compose:
```bash
make up                  # Start all services (db, geoserver, django)
make django-logs         # Follow Django logs
make django-shell        # Open Django shell in container
make django-migrate      # Run migrations
make django-test         # Run pytest suite
```

See [Makefile](../Makefile) for all commands. The Makefile handles:
- Environment file selection (ENV=dev or ENV=prod)
- Docker Compose file selection
- Service orchestration

### Testing
- Framework: pytest + pytest-django
- Settings: `tosca_api.settings.test` (uses SQLite in-memory)
- Run tests: `make django-test` or `pytest` (if running locally with uv)
- Test naming: `test_*.py` or `*_tests.py`

Example test pattern from [tosca_api/apps/authentication/tests/test_token_verification.py](../tosca_api/apps/authentication/tests/test_token_verification.py):
- Generate real RSA keys for JWT signing
- Mock JWKS endpoint with actual public key
- Test both valid and invalid token scenarios

### Migrations
Always create migrations for model changes:
```bash
make django-makemigrations         # Create migrations
make django-makemigrations APP=core  # For specific app
make django-migrate                # Apply migrations
```

## Code Conventions

### Logging Strategy
Follow [docs/development/logging-strategy.md](../docs/development/logging-strategy.md):
- Use `logger = logging.getLogger(__name__)` in each module
- **NEVER** log tokens, passwords, or sensitive data
- **ALWAYS** log authentication failures and security events
- Use structured logging with context (user_id, request_id)
- Use appropriate log levels (DEBUG for dev only, INFO for operations, WARNING/ERROR for issues)

### Models
- Inherit from `TimeStampedModel` (in [tosca_api/apps/core/models.py](../tosca_api/apps/core/models.py)) for automatic `created_at`/`updated_at` timestamps
- Use `from __future__ import annotations` for forward references
- Type hints on all methods (see Layer model in [tosca_api/apps/tosca_web/layers/models.py](../tosca_api/apps/tosca_web/layers/models.py))

### ViewSets
Standard pattern:
```python
class LayerViewSet(viewsets.ModelViewSet):
    queryset = Layer.objects.select_related("owner")  # Always optimize queries
    serializer_class = LayerSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)  # Inject current user
```

### Git Workflow
- Follow Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`
- Branch naming: `feat/description`, `fix/bug-name`, `docs/topic`
- Keep PRs small and focused
- Target all PRs to `main` branch

See [docs/development/git-workflow.md](../docs/development/git-workflow.md).

## Key Files Reference

- [tosca_api/settings/base.py](../tosca_api/settings/base.py) - Core Django settings, DRF config, Keycloak setup
- [tosca_api/apps/authentication/backends.py](../tosca_api/apps/authentication/backends.py) - Keycloak OAuth2 adapter, JWT auth backend
- [tosca_api/apps/core/jwt_utils.py](../tosca_api/apps/core/jwt_utils.py) - JWT verification logic
- [Makefile](../Makefile) - All Docker and development commands
- [project_structure.md](../project_structure.md) - Detailed repository layout

## Common Patterns

### Adding a New API Endpoint
1. Create model in `tosca_api/apps/tosca_web/{module}/models.py`
2. Create serializer in `serializers.py`
3. Create viewset in `views.py`
4. Register routes in `urls.py`
5. Include in parent URLconf if needed
6. Create tests in `tests/test_{module}.py`
7. Run `make django-makemigrations` and `make django-migrate`

### Working with JWT Tokens
Use `verify_and_decode_token()` from `tosca_api.apps.core.jwt_utils` - it handles:
- JWKS fetching and caching
- Signature verification
- Issuer/audience validation
- Token expiration checking

### Permission Checking
Default: `permissions.IsAuthenticated` for all DRF views. Override per-view if needed. Custom permissions go in `tosca_api/apps/core/permissions.py`.

## Troubleshooting

### "No module named 'tosca_api'"
Ensure you're using the correct settings module. Check `DJANGO_SETTINGS_MODULE` environment variable or use `--settings` flag.

### Database Connection Errors
Verify `.env.dev` contains correct `PG_HOST`, `PG_PORT`, `PG_DATABASE`, `PG_API_USER`, `PG_API_PASSWORD`. For Docker: `make down && make up` to restart services.

### JWT Verification Failures
Check that `KEYCLOAK_JWKS_URL` and `KEYCLOAK_ISSUER` in settings match your Keycloak realm configuration. Verify token audience matches `ALLOWED_TOKEN_AUDIENCES`.
