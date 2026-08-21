# TOSCA Django API

TOSCA is a modular Django API backend. It contains the Django admin and API
surface for geodata provider management, catalog data, campaigns, events,
feedback, geocontext, geostories, feature links, and authentication.

The local development stack runs three core services:

- Django API
- PostgreSQL/PostGIS
- GeoServer

The important rule: after setting up the environment, always initialize the
Django project database before using the admin or API. Missing migrations are
the most common cause of errors such as missing columns in admin pages.

## Quick Start

Create the dev environment file, select it, then initialize the project:

```bash
cp .env.example .env.dev
make set-env ENV=dev
make which-env
make initialize-project
```

`make initialize-project` does the first boot work:

- builds Docker images
- starts PostgreSQL/PostGIS, GeoServer, and Django
- runs Django migrations
- restarts Django after the database schema is ready

After that, create a Django admin user if needed:

```bash
make django-createsuperuser
```

Then open:

- Django admin: `http://localhost:8000/admin/`
- GeoServer: `http://localhost:8080/geoserver/web/`

## Daily Development

Start services:

```bash
make up
```

Stop services:

```bash
make down
```

Follow logs:

```bash
make logs
make django-logs
```

Show running containers:

```bash
make ps
```

Rebuild after dependency, Dockerfile, or image changes:

```bash
make rebuild
```

## Django Database Workflow

This repository is a Django project first. Treat migrations as part of the
normal development loop.

After pulling code, switching branches, or enabling a feature that adds model
fields:

```bash
make django-migrate
```

For one app only:

```bash
make django-migrate APP=geodata_providers
```

To create migrations after model changes:

```bash
make django-makemigrations
make django-migrate
```

If the admin raises a database error like `column ... does not exist`, first
check migration state and apply pending migrations:

```bash
docker compose --env-file .env.dev -f docker-compose-dev.yml exec django \
  uv run python manage.py showmigrations geodata_providers

make django-migrate
```

## Common Django Commands

```bash
make django-shell
make django-cmd
make django-migrate
make django-makemigrations
make django-createsuperuser
make django-test
make django-test-integration
```

Full command list:

```bash
make help
```

## Environment Selection

The Makefile uses `.make.env` to remember the active environment.

Development:

```bash
make set-env ENV=dev
make which-env
```

Production:

```bash
cp .env.example .env.prod
make set-env ENV=prod
make which-env
```

Environment resolution:

- `dev` uses `.env.dev` and `docker-compose-dev.yml`
- `prod` uses `.env.prod` and `docker-compose-prod.yml`

Keep `ENV`, `ENV_TYPE`, `ENV_FILE`, ports, passwords, and public URLs aligned in
the selected env file.

## Secrets and Env Files

Only `.env.example` is committed, and it holds placeholders only (every
credential is `CHANGE_ME_...`). `.env`, `.env.dev`, `.env.prod`, and
`.env.test` are gitignored deliberately — none of them should ever be
committed, since they carry real (even if dev-only) credentials.

A pre-commit hook enforces this: it blocks committing any `.env*` file other
than `.env.example`. Set it up once per clone:

```bash
uv sync --group dev
uv run pre-commit install
```

### Rotating persisted Postgres service-role passwords

`PG_API_PASSWORD` and `PG_GS_PASSWORD` are used when the Postgres volume is
first initialized. Changing either value later does not change the existing
Postgres role, because the volume persists across container recreation.

After intentionally changing either value in the selected environment file,
apply it explicitly:

```bash
make reconcile-postgis-service-passwords CONFIRM=1
make restart
```

The command updates the existing `tosca_api` and `tosca_gs` service roles from
the selected environment and never prints their passwords. It fails if the
database has not been initialized. It is deliberately not part of `make up` or
container startup, so a stale or accidentally selected environment cannot
silently rotate credentials.

The operation streams its script from the checkout where `make` is run and
forwards the selected environment explicitly. It therefore does not depend on
which worktree originally created an already-running `db` container.

## Keycloak OIDC Note

When using Keycloak/OIDC, the Keycloak client's valid redirect URIs and the
realm's browser security headers must both allow the public Django callback
origin.

Chrome may stop after the Keycloak login POST if the realm
`Content-Security-Policy` does not allow the Django site in `form-action`. In
Network this looks like Keycloak returning `302 Found` with a `Location` header
pointing at `/accounts/oidc/.../callback/`, but Chrome never makes the callback
request. Firefox may appear more forgiving, and refreshing can work because the
Keycloak SSO session already exists.

Check the Keycloak realm setting:

```text
Realm settings -> Security defenses -> Headers -> Content-Security-Policy
```

The `form-action` directive must include the Django public origin. For multiple
deployment subdomains, keep the policy explicit where possible:

```text
form-action 'self' https://<django-public-origin> https://<other-allowed-origin>
```

If all trusted deployments live below the same controlled DNS zone, a wildcard
can be used, but it is broader than an explicit allowlist:

```text
form-action 'self' https://*.example.org
```

## Docker Model

Development and production both use the CI-published GeoServer image from
GitHub Container Registry. Pin `GEOSERVER_VERSION` in the active environment
file to the same tested release in both environments; do not use `latest` for
deployments that need reproducibility.

GeoServer Docker implementation changes belong in the
`digitalcityscience/geoserver_docker` repository. Publish and test a new image
there, then update `GEOSERVER_VERSION` here to promote it.

## GeoServer Admin Bootstrap

GeoServer admin credentials are configured in the active env file:

```dotenv
GEOSERVER_ADMIN_USER=admin2
GEOSERVER_ADMIN_PASSWORD=geoserver2
```

On first startup the container uses GeoServer's built-in `admin/geoserver` only
as a bootstrap credential. It then creates or updates `GEOSERVER_ADMIN_USER`,
grants `ADMIN`, and disables the built-in `admin` user by default when the
configured user is not `admin`.

## GeoServer JDBC Settings

The JDBC feature flags are related but independent:

```dotenv
GEOSERVER_ENABLE_JDBC_ROLE=true
GEOSERVER_ENABLE_JDBC_AUTH=false
GEOSERVER_ENABLE_JDBC_CONFIG=false
```

- `GEOSERVER_ENABLE_JDBC_ROLE=true` enables JDBC role service files and role
  mapping into Postgre.
- `GEOSERVER_ENABLE_JDBC_AUTH=true` enables JDBC user/group service and auth
  provider. It implies role support.
- `GEOSERVER_ENABLE_JDBC_CONFIG=true` enables the advanced JDBCConfig catalog
  path.

JDBC setup is part of the published GeoServer image. Configure it through the
`GEOSERVER_ENABLE_JDBC_*` environment flags and consult the GeoServer image
repository for its operational documentation.

## Tests

Run the normal test suite:

```bash
make django-test
```

Run tests for a single app:

```bash
make django-test APP=geodata_providers
```

Run integration tests that require live services:

```bash
make django-test-integration
```

## Production Notes

The production stack is defined in `docker-compose-prod.yml`.

Services:

- `db`: PostgreSQL/PostGIS for Django.
- `geoserver`: production GeoServer image.
- `django`: Gunicorn-backed Django API.
- `web`: SPA Nginx container. Set `WEB_IMAGE` to the built SPA image.
- `nginx`: public reverse proxy for `/api/`, `/admin/`, `/accounts/`,
  `/geoserver/`, `/media/`, `/static/`, and the SPA shell.

Start production:

```bash
cp .env.example .env.prod
make set-env ENV=prod
make up
```

In a real deployment, set `WEB_IMAGE` to an immutable image from the SPA
pipeline:

```dotenv
WEB_IMAGE=registry.example.com/tosca-web:2026-05-21
```

Required Django media/static settings for production:

```dotenv
DJANGO_STATIC_URL=/static/
DJANGO_STATIC_ROOT=/app/staticfiles
DJANGO_MEDIA_URL=/media/
DJANGO_MEDIA_ROOT=/app/media
```

## SPA Media And Static Files

Production Django writes uploaded media and collected static files to shared
Docker volumes. Nginx serves those files to browsers:

```text
Django /app/media        -> media_files  -> Nginx /usr/share/nginx/media
Django /app/staticfiles  -> static_files -> Nginx /usr/share/nginx/staticfiles
```

The API may return media paths such as `/media/geocontext/editorjs/image.png`.
In same-domain deployments those paths work directly. In separate SPA/API host
deployments, the SPA should prepend the public API or asset host.

## Destructive Reset

To intentionally remove local database and GeoServer volumes:

```bash
make rmvolumes
make initialize-project
```

This deletes local data. Use it only when you really want a fresh stack.
