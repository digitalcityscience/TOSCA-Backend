# TOSCA-Web API

TOSCA Django REST backend for the TOSCA-Web geospatial platform.

## Quick Start

1. **Clone & Enter Directory**

   ```bash
   git clone <repo-url>
   cd dcs-django-api
   ```

2. **Choose Environment**
   - Default: `dev`
   - For production: `make ENV=prod ...`
   - Copy .env.exampe to env.dev

3. **Initialize Project (build, start, migrate)**

   ```bash
   make initialize-project
   ```

4. **Activate GeoServer JDBC Settings**

   ```bash
   make jdbc-settings-activation
   ```

5. **Create Django Superuser**

   ```bash
   make django-createsuperuser
   ```

6. **View Logs**
   ```bash
   make logs         # All services
   make django-logs  # Django only
   ```

## Common Makefile Commands

- `make up` / `make down` — Start/stop all services
- `make build` / `make rebuild` — Build/rebuild Docker images
- `make django-shell` — Open Django shell
- `make django-migrate` — Run migrations
- `make django-test` — Run Django tests
- `make uv-sync` — Sync Python dependencies

See `make help` for all available commands.
