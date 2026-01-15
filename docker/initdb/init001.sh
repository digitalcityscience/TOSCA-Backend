#!/bin/bash
set -e

# Environment variables from docker-compose.yml
API_USER="${PG_API_USER:-tosca_api}"
API_PASS="${PG_API_PASSWORD:-postgres_api}"
GS_USER="${PG_GS_USER:-tosca_gs}"
GS_PASS="${PG_GS_PASSWORD:-postgres_gs}"

SCHEMA_API="${PG_SCHEMA_API:-api_schema}"
SCHEMA_GS="${PG_SCHEMA_GEOSERVER:-gs_schema}"
SCHEMA_GIS="${PG_SCHEMA_GIS:-gis_schema}"

echo "Initializing database with schemas: $SCHEMA_API, $SCHEMA_GS, $SCHEMA_GIS"
echo "Creating users: $API_USER, $GS_USER"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- 0. PostGIS (ÖNCE BU)
    CREATE EXTENSION IF NOT EXISTS postgis;

    -- 1. Schemas
    CREATE SCHEMA IF NOT EXISTS $SCHEMA_API;
    CREATE SCHEMA IF NOT EXISTS $SCHEMA_GS;
    CREATE SCHEMA IF NOT EXISTS $SCHEMA_GIS;

    -- 2. Roles (varsa geç)
    DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$API_USER') THEN
        CREATE ROLE $API_USER LOGIN PASSWORD '$API_PASS';
      END IF;

      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$GS_USER') THEN
        CREATE ROLE $GS_USER LOGIN PASSWORD '$GS_PASS';
      END IF;
    END
    \$\$;

    -- 3. Permissions
    GRANT ALL ON SCHEMA $SCHEMA_API TO $API_USER;
    GRANT ALL ON SCHEMA $SCHEMA_GS  TO $GS_USER;
    GRANT USAGE ON SCHEMA $SCHEMA_GIS TO $GS_USER;

    -- 4. search_path (KRİTİK)
    ALTER ROLE $API_USER SET search_path = $SCHEMA_API, public;
    ALTER ROLE $GS_USER  SET search_path = $SCHEMA_GS, public;

    -- 5. PostGIS public schema permissions
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO $GS_USER;
    GRANT SELECT ON spatial_ref_sys TO $GS_USER;
    
    -- 6. GeoServer Disk Quota için public schema'da tablo oluşturma izni
    GRANT CREATE ON SCHEMA public TO $GS_USER;
    GRANT ALL ON ALL TABLES IN SCHEMA public TO $GS_USER;
    GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO $GS_USER;
    
    -- Future tables için de izin ver
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO $GS_USER;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO $GS_USER;
EOSQL

echo "Database initialization completed successfully!"
