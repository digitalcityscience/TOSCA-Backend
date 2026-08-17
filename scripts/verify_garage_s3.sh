#!/bin/sh
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose-dev.yml}"
ENV_FILE="${ENV_FILE:-.env.dev}"
DJANGO_STORAGE_BACKEND="${DJANGO_STORAGE_BACKEND:-s3}"
S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-http://garage:3900}"
S3_REGION_NAME="${S3_REGION_NAME:-garage}"
S3_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID:-GKtosca-local-django}"
S3_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY:-tosca-local-django-secret-change-me}"
S3_BUCKET_NAME="${S3_TEST_BUCKET_NAME:-tosca-media-test-private}"
S3_PUBLIC_BUCKET_NAME="${S3_TEST_PUBLIC_BUCKET_NAME:-tosca-media-test-public}"
S3_ARCHIVE_BUCKET_NAME="${S3_TEST_ARCHIVE_BUCKET_NAME:-tosca-media-test-archive}"
S3_ADDRESSING_STYLE="${S3_ADDRESSING_STYLE:-path}"
export COMPOSE_FILE ENV_FILE

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up --build -d garage
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build garage-bootstrap
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm -T --no-deps \
  -e GARAGE_PRIVATE_BUCKET="$S3_BUCKET_NAME" \
  -e GARAGE_PUBLIC_BUCKET="$S3_PUBLIC_BUCKET_NAME" \
  -e GARAGE_ARCHIVE_BUCKET="$S3_ARCHIVE_BUCKET_NAME" \
  -e GARAGE_ACCESS_KEY="$S3_ACCESS_KEY_ID" \
  garage-bootstrap

run_django_e2e() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm -T --no-deps --entrypoint "" \
    -e DJANGO_STORAGE_BACKEND="$DJANGO_STORAGE_BACKEND" \
    -e S3_ENDPOINT_URL="$S3_ENDPOINT_URL" \
    -e S3_REGION_NAME="$S3_REGION_NAME" \
    -e S3_ACCESS_KEY_ID="$S3_ACCESS_KEY_ID" \
    -e S3_SECRET_ACCESS_KEY="$S3_SECRET_ACCESS_KEY" \
    -e S3_BUCKET_NAME="$S3_BUCKET_NAME" \
    -e S3_PUBLIC_BUCKET_NAME="$S3_PUBLIC_BUCKET_NAME" \
    -e S3_ARCHIVE_BUCKET_NAME="$S3_ARCHIVE_BUCKET_NAME" \
    -e S3_ADDRESSING_STYLE="$S3_ADDRESSING_STYLE" \
    django uv run python scripts/garage_e2e.py "$1"
}

run_django_e2e write

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" restart garage
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up --wait garage
run_django_e2e read

printf '%s\n' 'Garage end-to-end persistence check passed.'
