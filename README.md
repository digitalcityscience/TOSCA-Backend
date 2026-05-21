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

## Production Docker Compose

The production stack is defined in `docker-compose-prod.yml`. It includes the Django API deployment plus the production GeoServer image published from GitHub Container Registry.

Services:

- `db`: PostgreSQL/PostGIS for Django.
- `geoserver`: GeoServer from `ghcr.io/digitalcityscience/tosca-geoserver:latest`. Production always pulls this published image; it does not build from `docker/geoserver_docker`.
- `django`: Gunicorn-backed Django API. Uploaded media is stored under `/app/media`; collected static files are stored under `/app/staticfiles`.
- `web`: SPA Nginx container. Use `WEB_IMAGE` to point to the production image that contains the built SPA assets.
- `nginx`: public reverse proxy. It routes `/api/`, `/admin/`, and `/accounts/` to Django, `/geoserver/` to GeoServer, routes the SPA shell to `web`, and serves `/media/` and `/static/` directly.

Shared named volumes:

- `media_files`: mounted at `/app/media` in Django and `/usr/share/nginx/media` in Nginx-based containers.
- `static_files`: mounted at `/app/staticfiles` in Django and `/usr/share/nginx/staticfiles` in Nginx-based containers.

The production GeoServer service is equivalent to:

```bash
docker pull ghcr.io/digitalcityscience/tosca-geoserver:latest
```

`docker-compose-prod.yml` sets `pull_policy: always` for this service so each production deploy checks the published latest image.

Required Django media/static settings for production:

```dotenv
DJANGO_STATIC_URL=/static/
DJANGO_STATIC_ROOT=/app/staticfiles
DJANGO_MEDIA_URL=/media/
DJANGO_MEDIA_ROOT=/app/media
```

Start production with:

```bash
cp .env.example .env.prod
make set-env ENV=prod
make up
```

In a real deployment, set `WEB_IMAGE` to the immutable image produced by the SPA pipeline, for example:

```dotenv
WEB_IMAGE=registry.example.com/tosca-web:2026-05-21
```

If you need to test a local SPA build, create a temporary compose override that mounts your built `dist` directory into `/usr/share/nginx/html` for the `web` service. Do not use that pattern as the normal production deployment method.

## SPA Integration And Media/Static Serving

The SPA must load uploaded media and collected static assets from production Nginx, not directly from the Django/Gunicorn container. Django writes files to `/app/media` and `/app/staticfiles`; Nginx serves the same files through shared Docker named volumes:

```text
Django /app/media        -> media_files  -> Nginx /usr/share/nginx/media       -> https://yourdomain.com/media/...
Django /app/staticfiles  -> static_files -> Nginx /usr/share/nginx/staticfiles -> https://yourdomain.com/static/...
```

The Nginx configs use `alias` for these paths:

```nginx
location /media/ {
    alias /usr/share/nginx/media/;
}

location /static/ {
    alias /usr/share/nginx/staticfiles/;
}
```

Integration scenarios:

- Same domain/host: if the SPA and Django API are served under `https://yourdomain.com`, media URLs returned by the API such as `/media/geocontext/editorjs/image.png` work directly in the browser as `https://yourdomain.com/media/geocontext/editorjs/image.png`.
- Different host/domain: if the SPA is served from another host, for example `https://app.yourdomain.com`, the public production Nginx that owns `https://yourdomain.com/media/...` must mount the same `media_files` volume and serve it. The SPA should render absolute media URLs such as `https://yourdomain.com/media/geocontext/editorjs/image.png`, or prepend the configured API/public asset base URL to relative `/media/...` paths.

Example flow:

1. A user uploads an image through the Django API.
2. Django stores the file in `/app/media/geocontext/editorjs/example.png`.
3. The API returns `/media/geocontext/editorjs/example.png`.
4. The SPA renders `https://yourdomain.com/media/geocontext/editorjs/example.png`.
5. Production Nginx serves the file from `/usr/share/nginx/media/geocontext/editorjs/example.png` through the shared `media_files` volume.

The same rule applies to Django static assets collected by `collectstatic`: they are served from `https://yourdomain.com/static/...` by Nginx through the shared `static_files` volume.

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

## Sık Sorulan Sorular

### SPA ile media entegrasyonu neden böyle?

Production ortamında Django/Gunicorn dosya sunucusu olarak kullanılmamalıdır. Django upload edilen dosyaları üretir ve diske yazar; tarayıcıya yüksek performanslı, cache edilebilir dosya servisini Nginx yapar. Bu yüzden SPA, media dosyalarını doğrudan Django container'ından değil, production Nginx üzerinden almalıdır. Docker ortamında bunun çalışması için Django'nun yazdığı `/app/media` dizini ile Nginx'in servis ettiği `/usr/share/nginx/media` dizini aynı named volume'a bağlanır. Böylece API'nin döndürdüğü `/media/...` URL'leri hem aynı domain senaryosunda hem de ayrı SPA host senaryosunda tutarlı şekilde çalışır.
