#!/usr/bin/env bash
set -euo pipefail

echo "=============================================="
echo "GeoServer ENTRYPOINT (deterministic security)"
echo "=============================================="

# ------------------------------------------------
# 1) PRE-START: prepare role service files
# ------------------------------------------------
if [ -f "/geoserver-init/setup-role-service.sh" ]; then
  echo "[1/3] Preparing JDBC Role Service (PRE-START)"
  /geoserver-init/setup-role-service.sh
fi

# ------------------------------------------------
# 2) START GeoServer (background)
# ------------------------------------------------
echo "[2/3] Starting GeoServer..."
/scripts/entrypoint.sh "$@" &
GEOSERVER_PID=$!

# ------------------------------------------------
# 3) POST-START: init JDBC roles (DB + REST)
# ------------------------------------------------
if [ -f "/geoserver-init/init-jdbc-roles.sh" ]; then
  echo "[3/3] Initializing JDBC roles (POST-START)"
  /geoserver-init/init-jdbc-roles.sh
fi

# ------------------------------------------------
# 4) Keep container alive
# ------------------------------------------------
wait $GEOSERVER_PID