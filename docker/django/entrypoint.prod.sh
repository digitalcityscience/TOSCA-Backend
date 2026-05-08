#!/usr/bin/env bash
set -euo pipefail

cd /app

APP_USER="appuser"
APP_GROUP="appuser"

mkdir -p /app/staticfiles /app/media /app/logs
chown -R "${APP_USER}:${APP_GROUP}" /app/staticfiles /app/media /app/logs /venv

run_as_appuser() {
  gosu "${APP_USER}:${APP_GROUP}" "$@"
}

if [ ! -f pyproject.toml ]; then
  echo "❌ pyproject.toml NOT FOUND in /app"
  ls -la
  exit 1
fi

echo "🔄 Syncing production dependencies..."
run_as_appuser uv sync --no-dev

echo "📦 Running database migrations..."
run_as_appuser uv run python manage.py migrate --noinput

echo "🗂️ Collecting static files..."
run_as_appuser uv run python manage.py collectstatic --noinput

if [ "${RUN_SETUP_DEFAULT_ENGINE:-true}" = "true" ]; then
  echo "🔧 Setting up default GeoServer engine..."
  run_as_appuser uv run python manage.py setup_default_engine
fi

echo "🚀 Starting Gunicorn..."
exec gosu "${APP_USER}:${APP_GROUP}" "$@"