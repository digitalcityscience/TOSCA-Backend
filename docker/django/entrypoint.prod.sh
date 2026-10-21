#!/usr/bin/env bash
set -euo pipefail

cd /app

APP_USER="appuser"
APP_GROUP="appuser"

mkdir -p /app/media /app/static /app/staticfiles /app/logs
chown -R "${APP_USER}:${APP_GROUP}" /app/media /app/static /app/staticfiles /app/logs /venv

run_as_appuser() {
  gosu "${APP_USER}:${APP_GROUP}" "$@"
}

if [ ! -f pyproject.toml ]; then
  echo "❌ pyproject.toml NOT FOUND in /app"
  ls -la
  exit 1
fi

if [ "${RUN_UV_SYNC_ON_STARTUP:-false}" = "true" ]; then
  echo "🔄 Syncing production dependencies..."
  run_as_appuser uv sync --frozen --no-dev
fi

echo "🔒 Validating production settings..."
run_as_appuser /venv/bin/python manage.py check --deploy

if [ "${RUN_MIGRATIONS_ON_STARTUP:-false}" = "true" ]; then
  echo "📦 Running database migrations..."
  run_as_appuser /venv/bin/python manage.py migrate --noinput
fi

if [ "${CONFIGURE_STORAGE_CORS_ON_STARTUP:-false}" = "true" ]; then
  echo "🌐 Configuring browser access to S3 media..."
  run_as_appuser /venv/bin/python manage.py configure_storage_cors
fi

if [ "${RUN_COLLECTSTATIC_ON_STARTUP:-true}" = "true" ]; then
  echo "🗂️ Collecting static files..."
  run_as_appuser /venv/bin/python manage.py collectstatic --noinput
fi

if [ "${RUN_SETUP_DEFAULT_ENGINE:-true}" = "true" ]; then
  echo "🔧 Setting up default GeoServer engine..."
  run_as_appuser /venv/bin/python manage.py setup_default_engine
fi

if [ "${RUN_DEFAULT_GEODATA_PROVIDER_SYNC_ON_STARTUP:-true}" = "true" ]; then
  echo "🔄 Syncing the default GeoData provider..."
  run_as_appuser /venv/bin/python manage.py sync_geoserver --default
fi

echo "🚀 Starting Gunicorn..."
exec gosu "${APP_USER}:${APP_GROUP}" "$@"
