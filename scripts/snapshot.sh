#!/usr/bin/env bash
#
# scripts/snapshot.sh — P0 snapshot/restore orchestrator (thin shell).
#
# Single-command, same-host, holistic restore point taken before
# deploy/upgrade/migration. P0 scope = Postgres (pg_dump -Fc) + GeoServer
# geoserver_data volume + manifest/verify. See
# docs/development/p0-snapshot-restore-spec.md and the ticket set under
# docs/development/p0-snapshot-restore/issues/.
#
# Ticket 01 (scaffold): subcommand dispatch, env resolution, compose helper,
# concurrency lock, and a working `list`. create/restore/verify are stubs that
# later tickets fill in.
#
# Invoked by the Makefile, which passes ENV_FILE / COMPOSE_FILE / ENV:
#   ENV_FILE=.env.dev COMPOSE_FILE=docker-compose-dev.yml ENV=dev \
#     scripts/snapshot.sh <create|restore|list|verify> [args]

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
BACKUPS_DIR="${REPO_ROOT}/backups"
LOCK_FILE="${BACKUPS_DIR}/.lock"
TOOLING_VERSION="p0-snapshot/1"

# Services quiesced during snapshot/restore, per environment (db stays up).
QUIESCE_DEV="django geoserver"
QUIESCE_PROD="django geoserver web nginx"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
log()  { printf '%s\n' "$*" >&2; }
warn() { printf '⚠️  %s\n' "$*" >&2; }
die()  { printf '❌ %s\n' "$*" >&2; exit 1; }

usage() {
  cat >&2 <<'EOF'
Usage: scripts/snapshot.sh <command> [options]

Commands:
  create   [--label <label>]         Create a snapshot under backups/<id>/
  restore  --id <snapshot_id> [--yes] [--only postgres|geoserver]
                                     Restore a snapshot (destructive)
  verify   --id <snapshot_id>        Run lightweight integrity checks
  list                               List existing snapshots (manifest summaries)

Environment (passed by the Makefile via which-env):
  ENV_FILE       path to .env.<ENV>          (required)
  COMPOSE_FILE   path to docker-compose-<ENV>.yml (required)
  ENV            dev | prod                  (required)
EOF
  exit 2
}

# ---------------------------------------------------------------------------
# Environment resolution
# ---------------------------------------------------------------------------
# Consumes ENV / ENV_FILE / COMPOSE_FILE from the caller (Makefile which-env)
# and sources .env.<ENV> so PG_*, GEOSERVER_VERSION, S3_* are available.
resolve_env() {
  ENV="${ENV:-}"
  ENV_FILE="${ENV_FILE:-}"
  COMPOSE_FILE="${COMPOSE_FILE:-}"

  [ -n "$ENV" ]          || die "ENV is not set (expected dev|prod)."
  case "$ENV" in
    dev|prod) : ;;
    *) die "Invalid ENV='$ENV'. Allowed: dev, prod." ;;
  esac

  [ -n "$ENV_FILE" ]     || die "ENV_FILE is not set."
  [ -f "$ENV_FILE" ]     || die "Missing ENV_FILE: $ENV_FILE"
  [ -n "$COMPOSE_FILE" ] || die "COMPOSE_FILE is not set."
  [ -f "$COMPOSE_FILE" ] || die "Missing COMPOSE_FILE: $COMPOSE_FILE"

  # Load the env file (PG_*, GEOSERVER_VERSION, S3_*, ...) into the environment.
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a

  # Services to quiesce for this environment.
  case "$ENV" in
    dev)  QUIESCE_SERVICES="$QUIESCE_DEV" ;;
    prod) QUIESCE_SERVICES="$QUIESCE_PROD" ;;
  esac
}

# ---------------------------------------------------------------------------
# Compose helper — every compose call must go through this.
# ---------------------------------------------------------------------------
compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

# ---------------------------------------------------------------------------
# Concurrency lock (backups/.lock) — only create/restore acquire it.
# ---------------------------------------------------------------------------
acquire_lock() {
  mkdir -p "$BACKUPS_DIR"
  # noclobber makes the redirect fail atomically if the lock already exists.
  if ! ( set -o noclobber; printf '%s\n' "$$" > "$LOCK_FILE" ) 2>/dev/null; then
    die "Another snapshot/restore is in progress (lock: $LOCK_FILE, pid $(cat "$LOCK_FILE" 2>/dev/null || echo '?')). Remove it if stale."
  fi
  trap release_lock EXIT INT TERM
}

release_lock() {
  rm -f "$LOCK_FILE"
}

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
cmd_create() {
  local label=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --label) label="${2:-}"; shift 2 ;;
      --label=*) label="${1#*=}"; shift ;;
      *) die "create: unknown option '$1'" ;;
    esac
  done
  resolve_env
  acquire_lock
  # TODO(ticket 02): preflight, snapshot_id, metadata, quiesce, pg_dump,
  # geoserver tar, unquiesce, manifest. TODO(ticket 03): verify + suspect flag.
  die "create: not yet implemented (ticket 02). ENV=$ENV label='${label}'"
}

cmd_restore() {
  local id="" yes="" only=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --id) id="${2:-}"; shift 2 ;;
      --id=*) id="${1#*=}"; shift ;;
      --yes) yes="1"; shift ;;
      --only) only="${2:-}"; shift 2 ;;
      --only=*) only="${1#*=}"; shift ;;
      *) die "restore: unknown option '$1'" ;;
    esac
  done
  [ -n "$id" ] || die "restore: --id <snapshot_id> is required."
  resolve_env
  acquire_lock
  # TODO(ticket 04): preflight+checksum, version guard, pre-restore safety
  # snapshot, quiesce, pg fresh-DB restore, geoserver volume restore, ordered
  # restart, post-restore verify. TODO(ticket 06): --only. TODO(05): garage.
  die "restore: not yet implemented (ticket 04). id=$id yes='${yes}' only='${only}'"
}

cmd_verify() {
  local id=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --id) id="${2:-}"; shift 2 ;;
      --id=*) id="${1#*=}"; shift ;;
      *) die "verify: unknown option '$1'" ;;
    esac
  done
  [ -n "$id" ] || die "verify: --id <snapshot_id> is required."
  # verify is read-only: no env resolution / lock needed.
  # TODO(ticket 03): sha256 vs manifest, pg_restore -l, tar tzf, suspect.flag.
  die "verify: not yet implemented (ticket 03). id=$id"
}

# Read-only listing of existing snapshots (manifest summaries).
cmd_list() {
  printf '%-40s  %-7s  %-21s  %-5s  %s\n' "SNAPSHOT_ID" "KIND" "CREATED_AT_UTC" "ENV" "LABEL"
  if [ ! -d "$BACKUPS_DIR" ]; then
    printf '(no snapshots under %s)\n' "$BACKUPS_DIR"
    return 0
  fi
  local found=0 manifest
  for manifest in "$BACKUPS_DIR"/*/manifest.json; do
    [ -e "$manifest" ] || continue
    found=1
    local dir suspect=""
    dir="$(dirname "$manifest")"
    [ -e "${dir}/suspect.flag" ] && suspect=" (suspect)"
    if command -v jq >/dev/null 2>&1; then
      jq -r '[.snapshot_id, (.kind // "-"), (.created_at_utc // "-"), (.env // "-"), (.label // "-")] | @tsv' "$manifest" \
        | while IFS=$'\t' read -r sid kind created env label; do
            printf '%-40s  %-7s  %-21s  %-5s  %s%s\n' "$sid" "$kind" "$created" "$env" "$label" "$suspect"
          done
    else
      python3 - "$manifest" "$suspect" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
suspect = sys.argv[2]
print("%-40s  %-7s  %-21s  %-5s  %s%s" % (
    m.get("snapshot_id", "-"), m.get("kind", "-"),
    m.get("created_at_utc", "-"), m.get("env", "-"),
    m.get("label", "-"), suspect))
PY
    fi
  done
  if [ "$found" -eq 0 ]; then
    printf '(no snapshots under %s)\n' "$BACKUPS_DIR"
  fi
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
main() {
  [ $# -ge 1 ] || usage
  local cmd="$1"; shift
  case "$cmd" in
    create)  cmd_create  "$@" ;;
    restore) cmd_restore "$@" ;;
    verify)  cmd_verify  "$@" ;;
    list)    cmd_list    "$@" ;;
    -h|--help|help) usage ;;
    *) die "Unknown command '$cmd' (expected create|restore|verify|list)." ;;
  esac
}

main "$@"
