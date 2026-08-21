#!/usr/bin/env bash
set -euo pipefail

# Synchronize the persisted Postgres service-role passwords with the active
# Compose environment. This lives below docker-entrypoint-initdb.d so Postgres
# does not execute it automatically on first-volume initialization.

MODE="${1:-apply}"

# In normal container startup these are POSTGRES_USER and POSTGRES_DB. The
# explicit Make operation forwards the source environment's PG_* values so it
# remains correct even if an existing container was created from another
# worktree.
POSTGRES_USER="${POSTGRES_USER:-${PG_SUPERUSER:-}}"
POSTGRES_DB="${POSTGRES_DB:-${PG_DATABASE:-}}"

if [[ "$MODE" != "apply" && "$MODE" != "--dry-run" ]]; then
  echo "Usage: $0 [--dry-run]" >&2
  exit 64
fi

required_variables=(
  POSTGRES_USER
  POSTGRES_DB
  PG_API_USER
  PG_API_PASSWORD
  PG_GS_USER
  PG_GS_PASSWORD
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "Required environment variable '$variable_name' is not set." >&2
    exit 1
  fi
done

psql_base=(
  psql
  --no-psqlrc
  --set=ON_ERROR_STOP=1
  --username="$POSTGRES_USER"
  --dbname="$POSTGRES_DB"
)

assert_role_exists() {
  local role_name="$1"
  local result

  # psql only performs :variable interpolation for SQL read from stdin, not
  # for --command. Keep the role name in a psql variable so it is quoted as a
  # SQL literal rather than interpolated by the shell.
  result="$(printf '%s\n' "SELECT 1 FROM pg_roles WHERE rolname = :'role_name';" | \
    "${psql_base[@]}" --tuples-only --no-align --set=role_name="$role_name")"

  if [[ "$result" != "1" ]]; then
    echo "Postgres role '$role_name' does not exist. Initialize the database before reconciling passwords." >&2
    exit 1
  fi
}

assert_role_exists "$PG_API_USER"
assert_role_exists "$PG_GS_USER"

if [[ "$MODE" == "--dry-run" ]]; then
  echo "Validated existing service roles: '$PG_API_USER' and '$PG_GS_USER'. No passwords changed."
  exit 0
fi

# psql's identifier/literal interpolation quotes both role names and passwords.
# Password values are never echoed, written to a file, or interpolated by the shell.
"${psql_base[@]}" \
  --set=api_user="$PG_API_USER" \
  --set=api_password="$PG_API_PASSWORD" \
  --set=geoserver_user="$PG_GS_USER" \
  --set=geoserver_password="$PG_GS_PASSWORD" <<'SQL'
ALTER ROLE :"api_user" LOGIN PASSWORD :'api_password';
ALTER ROLE :"geoserver_user" LOGIN PASSWORD :'geoserver_password';
SQL

echo "Reconciled persisted passwords for service roles '$PG_API_USER' and '$PG_GS_USER'."
