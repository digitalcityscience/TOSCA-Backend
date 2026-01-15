#!/usr/bin/env bash
set -e

echo "=== GeoServer JDBC Role Service Setup via REST API ==="

GEOSERVER_URL="${GEOSERVER_URL:-http://localhost:8080/geoserver}"
ADMIN_USER="${GEOSERVER_ADMIN_USER:-admin}"
ADMIN_PASS="${GEOSERVER_ADMIN_PASSWORD:-geoserver}"
ROLE_SERVICE_NAME="${GS_ROLE_SERVICE_NAME:-db_roles}"

# Wait for GeoServer to be ready
echo "Waiting for GeoServer to start..."
MAX_WAIT=120
COUNTER=0
until curl -sf "${GEOSERVER_URL}/web/" > /dev/null 2>&1; do
  if [ $COUNTER -ge $MAX_WAIT ]; then
    echo "ERROR: GeoServer did not start in time"
    exit 1
  fi
  echo "  Waiting... ($COUNTER/$MAX_WAIT)"
  sleep 2
  COUNTER=$((COUNTER + 2))
done
echo "✓ GeoServer is running"

# Check if role service already exists
echo "Checking if role service '${ROLE_SERVICE_NAME}' exists..."
RESPONSE=$(curl -sf -u "${ADMIN_USER}:${ADMIN_PASS}" \
  "${GEOSERVER_URL}/rest/security/roles/${ROLE_SERVICE_NAME}.json" 2>/dev/null || echo "not_found")

if [[ "$RESPONSE" != "not_found" ]]; then
  echo "✓ Role service '${ROLE_SERVICE_NAME}' already exists"
else
  # Create JDBC Role Service via REST API
  echo "Creating JDBC Role Service via REST API..."

  # Template dosyalarını data_dir'e kopyala
  TARGET_DIR="/opt/geoserver/data_dir/security/role/${ROLE_SERVICE_NAME}"
  TEMPLATE_DIR="/geoserver-init/jdbc_role_service"

  mkdir -p "$TARGET_DIR"
  cp "$TEMPLATE_DIR/rolesddl.xml" "$TARGET_DIR/"
  cp "$TEMPLATE_DIR/rolesdml.xml" "$TARGET_DIR/"
  envsubst < "$TEMPLATE_DIR/config.xml.template" > "$TARGET_DIR/config.xml"

  # Ownership fix
  if id geoserveruser &>/dev/null; then
    chown -R geoserveruser:geoserverusers "$TARGET_DIR"
    chmod 750 "$TARGET_DIR"
    chmod 640 "$TARGET_DIR"/*
  fi

  echo "✓ Configuration files created at $TARGET_DIR"
fi

# Always set JDBC Role Service as active in security config
SECURITY_CONFIG="/opt/geoserver/data_dir/security/config.xml"
if [ -f "$SECURITY_CONFIG" ]; then
  sed -i "s|<roleServiceName>.*</roleServiceName>|<roleServiceName>${ROLE_SERVICE_NAME}</roleServiceName>|g" "$SECURITY_CONFIG"
  echo "✓ Updated security config to set active role service to '${ROLE_SERVICE_NAME}'"
else
  echo "WARNING: Security config file not found at $SECURITY_CONFIG"
fi

# Reload GeoServer configuration via REST API
echo "Reloading GeoServer configuration..."
curl -sf -u "${ADMIN_USER}:${ADMIN_PASS}" \
  -X POST "${GEOSERVER_URL}/rest/reload" || true

echo "✓ GeoServer configuration reloaded"

echo "=== JDBC Role Service setup complete ==="
