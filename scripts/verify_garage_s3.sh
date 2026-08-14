#!/bin/sh
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose-dev.yml}"
ENV_FILE="${ENV_FILE:-.env.dev}"
export DJANGO_STORAGE_BACKEND="${DJANGO_STORAGE_BACKEND:-s3}"
export S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-http://garage:3900}"
export S3_REGION_NAME="${S3_REGION_NAME:-garage}"
export S3_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID:-GKtosca-local-django}"
export S3_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY:-tosca-local-django-secret-change-me}"
export S3_BUCKET_NAME="${S3_BUCKET_NAME:-tosca-media-private}"
export S3_PUBLIC_BUCKET_NAME="${S3_PUBLIC_BUCKET_NAME:-tosca-media-public}"
export S3_ADDRESSING_STYLE="${S3_ADDRESSING_STYLE:-path}"
export COMPOSE_FILE ENV_FILE

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up --build -d garage garage-bootstrap django
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T django \
  uv run python scripts/garage_e2e.py write

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" restart garage
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up --wait garage
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T django \
  uv run python scripts/garage_e2e.py read

printf '%s\n' 'Garage end-to-end persistence check passed.'
