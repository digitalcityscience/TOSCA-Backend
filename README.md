# TOSCA Django API

Backend for the TOSCA geospatial platform. The local stack runs:

- PostgreSQL/PostGIS
- GeoServer
- Django API

## Docker Model

Development and production intentionally use different GeoServer image sources:

- `docker-compose-dev.yml` builds GeoServer from the local submodule at `docker/geoserver_docker`. Use this when changing GeoServer Docker scripts/templates and testing them quickly.
- `docker-compose.yml` is the production fallback compose and uses `ghcr.io/digitalcityscience/tosca-geoserver:latest`. Production should consume the CI-built image from the GeoServer Docker repository main branch.

That means GeoServer Docker changes should be validated in dev, merged in `geoserver_docker`, then picked up by the CI-built production image.

## Environment

Create an env file and select the active environment:

```bash
cp .env.example .env.dev
make set-env ENV=dev
make which-env
```

For production, create `.env.prod` and run:

```bash
make set-env ENV=prod
make which-env
```

The root Makefile resolves compose files as:

- `dev` -> `docker-compose-dev.yml`
- `prod` -> `docker-compose-prod.yml` if present, otherwise `docker-compose.yml`

## Start

```bash
make initialize-project
```

This builds images, starts containers, runs Django migrations, and restarts Django.

For day-to-day work:

```bash
make up
make down
make rebuild
make logs
make ps
```

`make rebuild`, `make up`, `make down`, and `make rmvolumes` all use the same active `ENV_FILE` and `COMPOSE_FILE` shown by `make which-env`.

## GeoServer Admin Bootstrap

GeoServer admin is controlled by:

```dotenv
GEOSERVER_ADMIN_USER=admin2
GEOSERVER_ADMIN_PASSWORD=geoserver2
```

On first startup the container uses GeoServer's built-in `admin/geoserver` only as a bootstrap credential. It then creates or updates `GEOSERVER_ADMIN_USER`, grants `ADMIN`, and disables the built-in `admin` user by default when the configured user is not `admin`.

This happens before JDBC role/auth/config activation.

## JDBC Feature Flags

The JDBC features are related but independent:

```dotenv
GEOSERVER_ENABLE_JDBC_ROLE=true
GEOSERVER_ENABLE_JDBC_AUTH=true
GEOSERVER_ENABLE_JDBC_CONFIG=false
```

- `GEOSERVER_ENABLE_JDBC_ROLE=true` enables JDBC role service files and role mapping.
- `GEOSERVER_ENABLE_JDBC_AUTH=true` enables JDBC user/group service and auth provider. It implies role support.
- `GEOSERVER_ENABLE_JDBC_CONFIG=true` enables the advanced JDBCConfig/catalog path.

`GEOSERVER_SECURITY_MODE` can still be used as a preset (`jdbc-role`, `jdbc-auth-role`, `jdbc-config`), but the `GEOSERVER_ENABLE_JDBC_*` flags are the actual feature toggles. Do not rely on mode alone when reviewing production env.

## JDBC Activation

After services are running, apply GeoServer JDBC security/config files:

```bash
make jdbc-settings-activation
```

This runs the activation script from `docker/geoserver_docker` with the active root env file.

For JDBC role/auth, complete the GeoServer UI steps described in:

- `docker/geoserver_docker/readme_jdbc.md`

## Plugins And Images

JDBC-related GeoServer extensions should be baked into the GeoServer Docker image by the GeoServer Docker repository CI. Production should not depend on slow runtime plugin downloads.

For local dev builds, `docker-compose-dev.yml` passes plugin build args from `.env.dev` into the submodule Dockerfile.

## Common Checks

If something looks wrong:

```bash
make which-env
make ps
make logs
make jdbc-settings-activation
```

For a fresh GeoServer security test, remove volumes intentionally:

```bash
make rmvolumes
make rebuild
```

Expected admin result when `GEOSERVER_ADMIN_USER=admin2`:

```text
admin2:<configured password> -> works
admin:geoserver              -> rejected
```

## Django Commands

```bash
make django-migrate
make django-createsuperuser
make django-shell
make django-test
make django-test-integration
```

Full command list:

```bash
make help
```
