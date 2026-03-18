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
   - Copy `.env.example` to `.env.dev` and fill in your values

   **Optional – local GeoServer data mount:**
   If you have local geodata to expose inside GeoServer at `/mnt/home`, add this
   line to your `.env.dev` (the file is gitignored, so it stays off GitHub):

   ```dotenv
   GEOSERVER_LOCAL_DATA_PATH=/absolute/path/to/your/geodata
   ```

   Leave it unset if you don't need it — GeoServer will start normally without it.

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
