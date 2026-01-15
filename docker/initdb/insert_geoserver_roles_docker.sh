#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# STRICT MODE:
# - .env MUST exist
# - NO silent fallbacks
# - Missing vars => FAIL FAST
###############################################################################

# ---------------------------------------------------------------------------
# .env required
# ---------------------------------------------------------------------------
if [ ! -f ".env" ]; then
  echo "❌ .env file is required but not found"
  exit 1
fi

source .env

# ---------------------------------------------------------------------------
# REQUIRED ENV CHECKS
# ---------------------------------------------------------------------------
: "${PG_HOST:?}"
: "${PG_PORT:?}"
: "${PG_DATABASE:?}"

: "${PG_SUPERUSER:?}"
: "${PG_SUPERPASS:?}"

: "${PG_SCHEMA_GEOSERVER:?}"

: "${GS_ADMIN_ROLE:?}"
: "${GS_GROUP_ADMIN_ROLE:?}"

# ---------------------------------------------------------------------------
# DB connection (only .env)
# ---------------------------------------------------------------------------
PGHOST="$PG_HOST"
PGPORT="$PG_PORT"
PGDATABASE="$PG_DATABASE"
PGUSER="$PG_SUPERUSER"
PGPASSWORD="$PG_SUPERPASS"
ROLE_SCHEMA="$PG_SCHEMA_GEOSERVER"

DB_CONTAINER=${DB_CONTAINER:-tosca-db}

echo "------------------------------------------------------------"
echo "GeoServer JDBC Role bootstrap (STRICT)"
echo "DB       : $PGDATABASE@$PGHOST:$PGPORT"
echo "Schema   : $ROLE_SCHEMA"
echo "User     : $PGUSER (SUPERUSER)"
echo "Roles    : $GS_ADMIN_ROLE, $GS_GROUP_ADMIN_ROLE"
echo "------------------------------------------------------------"

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
TMPSQL=$(mktemp /tmp/geoserver_roles.XXXX.sql)
trap 'rm -f "$TMPSQL"' EXIT

cat > "$TMPSQL" <<SQL
BEGIN;

CREATE SCHEMA IF NOT EXISTS "$ROLE_SCHEMA";

CREATE TABLE IF NOT EXISTS "$ROLE_SCHEMA".roles (
  name varchar(64) PRIMARY KEY,
  parent varchar(64)
);

INSERT INTO "$ROLE_SCHEMA".roles(name)
SELECT '$GS_ADMIN_ROLE'
WHERE NOT EXISTS (
  SELECT 1 FROM "$ROLE_SCHEMA".roles WHERE name = '$GS_ADMIN_ROLE'
);

INSERT INTO "$ROLE_SCHEMA".roles(name)
SELECT '$GS_GROUP_ADMIN_ROLE'
WHERE NOT EXISTS (
  SELECT 1 FROM "$ROLE_SCHEMA".roles WHERE name = '$GS_GROUP_ADMIN_ROLE'
);

COMMIT;
SQL

# ---------------------------------------------------------------------------
# RUN
# ---------------------------------------------------------------------------
if docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}\$"; then
  echo "[+] Running inside container: $DB_CONTAINER"
  docker cp "$TMPSQL" "$DB_CONTAINER":/tmp/roles.sql
  docker exec "$DB_CONTAINER" bash -lc "
    export PGPASSWORD='$PGPASSWORD'
    psql -U '$PGUSER' -d '$PGDATABASE' -v ON_ERROR_STOP=1 -f /tmp/roles.sql
    rm -f /tmp/roles.sql
  "
else
  echo "❌ DB container '$DB_CONTAINER' not running"
  exit 1
fi

echo "------------------------------------------------------------"
echo "✔ GeoServer roles ensured (STRICT MODE)"
echo "------------------------------------------------------------"