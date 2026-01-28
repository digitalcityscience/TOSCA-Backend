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
echo "🚀 Starting application..."
exec "$@"