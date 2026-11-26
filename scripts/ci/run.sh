#!/usr/bin/env bash
# Checks for a built tosca-backend image, run against throwaway PostGIS and
# Garage containers. Used by the PR and release workflows and by
# `make test` / `make lint` in the TOSCA platform repository.
#
#   scripts/ci/run.sh lint  IMAGE   ruff (RUFF_PATHS="a.py b.py" for some files only)
#   scripts/ci/run.sh test  IMAGE   pytest (all but `integration`), S3 tests on Garage
#   scripts/ci/run.sh smoke IMAGE   start the image as in production and probe it
#
# IMAGE is built from docker/django/Dockerfile.prod. lint and test install the
# dev dependencies into a throwaway container of it and run against this
# checkout, mounted at /app. smoke runs the image as it is.
#
# Everything this script starts lives on its own network and is removed at
# the end, volumes included; nothing else on the machine is touched.
set -Eeuo pipefail
cd "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/../.."

mode=${1:?usage: run.sh lint|test|smoke IMAGE}
image=${2:?usage: run.sh lint|test|smoke IMAGE}
POSTGIS_IMAGE=${POSTGIS_IMAGE:-postgis/postgis:16-3.4@sha256:44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5}
GARAGE_IMAGE=${GARAGE_IMAGE:-dxflrs/garage:v2.3.0}

id=tosca-backend-ci-$$
net=$id
failed=0

cleanup() {
  if [[ $failed != 0 && $mode == smoke ]]; then docker logs --tail 100 "$id-django" 2>&1 || true; fi
  # Containers are started with --rm, so stopping them removes their
  # anonymous volumes as well.
  docker stop "$id-db" "$id-garage" >/dev/null 2>&1 || true
  # Kept until here (no --rm) so its log survives a crash; the image declares
  # no volumes.
  docker rm -f "$id-django" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
}
trap cleanup EXIT

rand() { openssl rand -hex "$1"; }
pg_api_password=$(rand 16)
s3_key=GK$(rand 12)
s3_secret=$(rand 32)

# The settings a container of the image gets; the same names as in production.
common_env=(
  -e PG_HOST=db -e PG_DOCKER_PORT=5432 -e PG_DATABASE=tosca
  -e PG_API_USER=tosca_api -e "PG_API_PASSWORD=$pg_api_password"
  -e PG_SCHEMA_API=api_schema -e PG_SCHEMA_GIS=gis_schema
  -e "DJANGO_SECRET_KEY=$(rand 32)"
  -e "FIELD_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')"
  -e DJANGO_STORAGE_BACKEND=s3
  -e S3_ENDPOINT_URL=http://garage:3900 -e S3_REGION_NAME=garage
  -e "S3_ACCESS_KEY_ID=$s3_key" -e "S3_SECRET_ACCESS_KEY=$s3_secret"
  -e S3_ADDRESSING_STYLE=path -e S3_SIGNATURE_VERSION=s3v4
  -e S3_BUCKET_NAME=tosca-media-private -e S3_PUBLIC_BUCKET_NAME=tosca-media-public
  -e S3_ARCHIVE_BUCKET_NAME=tosca-media-archive
  -e GEOSERVER_INTERNAL_URL=http://geoserver.invalid:8080/geoserver
  -e GEOSERVER_PUBLIC_URL=http://geoserver.invalid:8080/geoserver
  -e GEOSERVER_ADMIN_USER=admin -e "GEOSERVER_ADMIN_PASSWORD=$(rand 16)"
)

start_services() {
  docker network create "$net" >/dev/null
  docker run --rm -d --name "$id-db" --network "$net" --network-alias db \
    -e POSTGRES_USER=postgres -e "POSTGRES_PASSWORD=$(rand 16)" -e POSTGRES_DB=tosca \
    -e PG_API_USER=tosca_api -e "PG_API_PASSWORD=$pg_api_password" \
    -e PG_GS_USER=tosca_gs -e "PG_GS_PASSWORD=$(rand 16)" \
    -v "$PWD/docker/postgis/init001.sh:/docker-entrypoint-initdb.d/init001.sh:ro" \
    "$POSTGIS_IMAGE" >/dev/null
  docker run --rm -d --name "$id-garage" --network "$net" --network-alias garage \
    -e "GARAGE_RPC_SECRET=$(rand 32)" -e "GARAGE_ADMIN_TOKEN=$(rand 32)" \
    -e "GARAGE_DEFAULT_ACCESS_KEY=$s3_key" -e "GARAGE_DEFAULT_SECRET_KEY=$s3_secret" \
    -v "$PWD/scripts/ci/garage.toml:/etc/garage.toml:ro" \
    "$GARAGE_IMAGE" /garage server --single-node --default-access-key >/dev/null

  echo "Waiting for PostGIS and Garage"
  for _ in $(seq 1 60); do
    # pg_isready is true during the init scripts too; wait for the role.
    if docker exec "$id-db" psql -U postgres -d tosca -tAc "select 1 from pg_roles where rolname='tosca_api'" 2>/dev/null | grep -q 1 \
       && docker exec "$id-db" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 \
       && docker exec "$id-garage" /garage status >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "PostGIS or Garage did not start" >&2
  docker logs --tail 50 "$id-db" >&2 || true
  docker logs --tail 50 "$id-garage" >&2 || true
  return 1
}

# Runs a command in a throwaway container of the image with the dev
# dependencies installed and this checkout at /app.
in_dev_container() {
  docker run --rm --network "$net" --user 0 --entrypoint "" "${common_env[@]}" "$@"
}

case $mode in
  lint)
    # shellcheck disable=SC2086 # RUFF_PATHS is a space-separated list
    docker run --rm --user 0 --entrypoint "" -v "$PWD:/app" -w /app "$image" \
      sh -c 'uv sync --frozen --quiet && exec /venv/bin/ruff check "$@"' ruff ${RUFF_PATHS:-tosca_api}
    ;;

  test)
    start_services
    # test settings expect a pre-created test_tosca database (pytest --reuse-db).
    docker exec "$id-db" psql -q -U postgres -d tosca -v ON_ERROR_STOP=1 \
      -c "CREATE DATABASE test_tosca OWNER tosca_api"
    docker exec "$id-db" psql -q -U postgres -d test_tosca -v ON_ERROR_STOP=1 \
      -c "CREATE EXTENSION IF NOT EXISTS postgis"
    in_dev_container -v "$PWD:/app" -w /app \
      -e S3_TEST_BUCKET_NAME=tosca-ci-private -e S3_TEST_PUBLIC_BUCKET_NAME=tosca-ci-public \
      -e S3_TEST_ARCHIVE_BUCKET_NAME=tosca-ci-archive \
      -e DJANGO_CORS_ALLOWED_ORIGINS=http://localhost:5173 \
      "$image" sh -c '
        set -e
        uv sync --frozen --quiet
        # The S3 test buckets have to exist before the storage tests run.
        DJANGO_SETTINGS_MODULE=tosca_api.settings.test /venv/bin/python manage.py init_storage --skip-cors
        exec /venv/bin/pytest -p no:cacheprovider -m "not integration" '"${PYTEST_ARGS:-}"
    ;;

  smoke)
    start_services
    port=${SMOKE_PORT:-18000}
    # Production settings, as the platform runs them. Sign-in needs a
    # Keycloak; the image refuses to start without one (system checks
    # E004-E006), so the smoke test gives it placeholder values. Nothing here
    # validates a token, and no check is switched off.
    docker run -d --name "$id-django" --network "$net" -p "127.0.0.1:$port:8000" \
      "${common_env[@]}" \
      -e ENV=prod -e DJANGO_SETTINGS_MODULE=tosca_api.settings.production \
      -e DJANGO_DEBUG=False -e DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost \
      -e DJANGO_CORS_ALLOWED_ORIGINS=http://web.example \
      -e KEYCLOAK_SERVER_URL=https://keycloak.invalid/ -e KEYCLOAK_REALM=tosca \
      -e KEYCLOAK_CLIENT_ID=tosca-backend \
      -e KEYCLOAK_ISSUER=https://keycloak.invalid/realms/tosca \
      -e KEYCLOAK_JWKS_URL=https://keycloak.invalid/realms/tosca/protocol/openid-connect/certs \
      -e ALLOWED_TOKEN_AUDIENCES=tosca-backend -e ALLOWED_TOKEN_CLIENTS=tosca-frontend \
      -e RUN_MIGRATIONS_ON_STARTUP=true -e CONFIGURE_STORAGE_CORS_ON_STARTUP=false \
      -e RUN_SETUP_DEFAULT_ENGINE=false -e RUN_DEFAULT_GEODATA_PROVIDER_SYNC_ON_STARTUP=false \
      "$image" >/dev/null

    base=http://127.0.0.1:$port
    echo "Waiting for the API in $image"
    for _ in $(seq 1 90); do
      [[ $(docker inspect -f '{{.State.Running}}' "$id-django" 2>/dev/null) == true ]] || break
      [[ $(curl -s -o /dev/null -w '%{http_code}' "$base/healthz" || true) == 200 ]] && break
      sleep 2
    done

    ok()   { printf '  ok    %s\n' "$1"; }
    fail() { printf '  FAIL  %s\n        %s\n' "$1" "${2:-}"; failed=1; }
    check() { if [[ $3 == *"$2"* ]]; then ok "$1"; else fail "$1" "expected '$2', got '${3:0:200}'"; fi; }

    check "process is alive (/healthz)"     200 "$(curl -s -o /dev/null -w '%{http_code}' "$base/healthz")"
    check "database reachable (/readyz)"    200 "$(curl -s -o /dev/null -w '%{http_code}' "$base/readyz")"
    label=$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.version"}}')
    check "reports the image version"       "\"version\": \"$label\"" "$(curl -s "$base/api/version")"
    check "catalog answers"                 200 "$(curl -s -o /dev/null -w '%{http_code}' "$base/api/v1/catalog/providers")"
    check "admin requires sign-in"          302 "$(curl -s -o /dev/null -w '%{http_code}' "$base/admin/")"
    check "unknown host is refused"         400 "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: evil.example' "$base/healthz")"
    # Warnings (e.g. no SSL redirect: TLS ends at the reverse proxy) are
    # allowed; any error fails.
    check "deploy checks pass (no errors)"  pass \
      "$(docker exec "$id-django" gosu appuser /venv/bin/python manage.py check --deploy >/dev/null 2>&1 && echo pass)"
    check "migrations applied"              "No planned migration operations" \
      "$(docker exec "$id-django" gosu appuser /venv/bin/python manage.py migrate --plan 2>&1 | tail -n 1)"
    manage() { docker exec "$id-django" gosu appuser /venv/bin/python manage.py "$@" 2>&1; }
    check "storage buckets created"         ": created" "$(manage init_storage --skip-cors)"
    check "storage init is idempotent"      3 "$(manage init_storage --skip-cors | grep -c ': exists')"
    echo
    if [[ $failed == 0 ]]; then echo "All checks passed."; else echo "Some checks FAILED."; fi
    exit $failed
    ;;

  *) echo "usage: run.sh lint|test|smoke IMAGE" >&2; exit 2 ;;
esac
