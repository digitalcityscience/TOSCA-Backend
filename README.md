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

## Docker Model

Development and production intentionally use different GeoServer image sources.

- `docker-compose-dev.yml` builds GeoServer from the local submodule at
  `docker/geoserver_docker`. Use this when changing GeoServer Docker scripts or
  templates.
- `docker-compose-prod.yml` uses the production GeoServer image published from
  GitHub Container Registry.

GeoServer Docker changes should be validated in dev, merged in
`docker/geoserver_docker`, then consumed through the CI-built production image.

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

After services are running, apply GeoServer JDBC security/config files when the
selected setup needs them:

```bash
make jdbc-settings-activation
```

For JDBC role/auth UI steps, see:

- `docker/geoserver_docker/readme_jdbc.md`

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
