#!/usr/bin/env bash
set -euo pipefail

echo "========================================="
echo "Pre-start GeoServer Configuration"
echo "========================================="

# GeoServer kullanıcı/grup
GS_USER="${GEOSERVER_USER:-geoserveruser}"
GS_GROUP="${GEOSERVER_GROUP:-geoserverusers}"
ROLE_NAME="${GS_ROLE_SERVICE_NAME:-jdbc}"
TARGET_DIR="/opt/geoserver/data_dir/security/role/${ROLE_NAME}"
TEMPLATE_DIR="/geoserver-init/jdbc_role_service"

echo "Configuring JDBC Role Service..."

# Zaten varsa dokunma
if [ -d "$TARGET_DIR" ]; then
  echo "Role service already exists at $TARGET_DIR"
  exit 0
fi

# Dizini oluştur
mkdir -p "$TARGET_DIR"

# XML'leri kopyala
cp "$TEMPLATE_DIR/rolesddl.xml" "$TARGET_DIR/"
cp "$TEMPLATE_DIR/rolesdml.xml" "$TARGET_DIR/"

# config.xml'i env ile render et
envsubst < "$TEMPLATE_DIR/config.xml.template" > "$TARGET_DIR/config.xml"

# Ownership ve permissions düzelt
if id "$GS_USER" &>/dev/null; then
  chown -R ${GS_USER}:${GS_GROUP} "$TARGET_DIR"
  chmod 750 "$TARGET_DIR"
  chmod 640 "$TARGET_DIR"/*
fi

echo "========================================="
echo "JDBC Role Service configured successfully!"
echo "GeoServer will load it on next start."
echo "========================================="
ls -la "$TARGET_DIR"
