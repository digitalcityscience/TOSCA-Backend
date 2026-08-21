#!/usr/bin/env bash
set -e
cd /app
echo "🔍 Checking project files..."

if [ ! -f pyproject.toml ]; then
  echo "❌ pyproject.toml NOT FOUND in /app"
  echo "📂 Contents of /app:"
  ls -la
  exit 1
fi

echo "✅ pyproject.toml found"
echo "🔄 Syncing dependencies with uv..."

if [[ "$DJANGO_SETTINGS_MODULE" == *"development"* ]]; then
  echo "➡ Installing DEV dependencies"
  uv sync --frozen --extra dev
else
  echo "➡ Installing PROD dependencies"
  uv sync --frozen
fi
echo "� Running migrations..."
uv run python manage.py migrate --noinput

echo "🔧 Setting up default GeoServer engine..."
uv run python manage.py setup_default_engine

if [ "${RUN_DEFAULT_GEODATA_PROVIDER_SYNC_ON_STARTUP:-true}" = "true" ]; then
  echo "🔄 Syncing the default GeoData provider..."
  uv run python manage.py sync_geoserver --default
fi

echo "�🚀 Starting application..."
exec "$@"
