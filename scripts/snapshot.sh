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
# concurrency lock, and a working `list`. Ticket 02: `create`. Ticket 03:
# `verify` + disk-space preflight + suspect flagging. Ticket 04: `restore`
# happy path (pre-restore safety snapshot, fresh-DB pg_restore, geoserver
# volume restore, ordered restart, post-restore verify). Selective restore
# (`--only`) is ticket 06; Garage reference check is ticket 05.
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

# Seconds to wait for a restarted service to become ready (see wait_ready).
HEALTH_TIMEOUT=300

# Services this run has stopped but not yet restarted. Read by cleanup_on_exit
# so a failure mid-flight doesn't leave django/geoserver stopped.
QUIESCED_SERVICES=""

# Set by do_snapshot on success — read by cmd_restore after taking the
# mandatory pre-restore safety snapshot (Q9).
LAST_SNAPSHOT_ID=""
LAST_SNAPSHOT_DIR=""

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
  trap cleanup_on_exit EXIT
  trap 'cleanup_on_exit; exit 130' INT
  trap 'cleanup_on_exit; exit 143' TERM
}

release_lock() {
  rm -f "$LOCK_FILE"
}

# EXIT/INT/TERM trap: on a non-zero exit, best-effort restart any services
# this run quiesced but never got around to restarting, then release the
# lock. QUIESCED_SERVICES is cleared once unquiesce completes normally.
# INT/TERM run this via an explicit `exit` (see acquire_lock) — a signal
# trap alone does not terminate the script, it would resume after the trap.
cleanup_on_exit() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ] && [ -n "$QUIESCED_SERVICES" ]; then
    warn "create/restore failed mid-flight — restarting quiesced services: $QUIESCED_SERVICES"
    # shellcheck disable=SC2086
    compose start $QUIESCED_SERVICES >/dev/null 2>&1 \
      || warn "Could not restart all quiesced services ($QUIESCED_SERVICES) — check manually."
  fi
  release_lock
}

# ---------------------------------------------------------------------------
# Portable helpers (macOS + Linux)
# ---------------------------------------------------------------------------
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

file_bytes() {
  stat -f%z "$1" 2>/dev/null || stat -c%s "$1"
}

# "healthy" if $1's container declares a healthcheck, else "true"/"false" for
# plain running state (e.g. dev django has no healthcheck). Empty if the
# container doesn't exist.
container_status() {
  local cid
  cid="$(compose ps -aq "$1")"
  [ -n "$cid" ] || return 0
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Running}}{{end}}' "$cid" 2>/dev/null || true
}

# Blocks until $1's container is ready (see container_status). Dies after $2
# seconds (default HEALTH_TIMEOUT). `docker compose start` does not block on
# healthchecks the way `up --wait` does, so callers need this poll loop.
wait_ready() {
  local service="$1" timeout="${2:-$HEALTH_TIMEOUT}" waited=0 status
  status="$(container_status "$service")"
  [ -n "$status" ] || die "$service: container not found while waiting for it to become ready."
  while :; do
    case "$status" in
      healthy|true) return 0 ;;
    esac
    [ "$waited" -lt "$timeout" ] || die "$service: did not become ready within ${timeout}s (last status: '${status}')."
    sleep 2
    waited=$((waited + 2))
    status="$(container_status "$service")"
  done
}

# Preflight check: is $1's container up and (if it declares one) healthy,
# right now — no waiting.
is_ready_now() {
  local status
  status="$(container_status "$1")"
  [ "$status" = "healthy" ] || [ "$status" = "true" ]
}

# Was $1 in the space-separated $QUIESCED_SERVICES list this run stopped?
was_quiesced() {
  case " $QUIESCED_SERVICES " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

# Stops the services in $QUIESCE_SERVICES that are actually running right now
# (skips ones already down, so a mid-flight failure doesn't resurrect
# something that wasn't this run's doing) and records them in
# $QUIESCED_SERVICES for restart_quiesced / cleanup_on_exit. db is never in
# $QUIESCE_SERVICES — it stays up through create and restore alike.
quiesce_now() {
  QUIESCED_SERVICES=""
  local svc
  for svc in $QUIESCE_SERVICES; do
    is_ready_now "$svc" && QUIESCED_SERVICES="${QUIESCED_SERVICES:+$QUIESCED_SERVICES }$svc"
  done
  log "Quiescing: ${QUIESCED_SERVICES:-<none running>} (db stays up)…"
  if [ -n "$QUIESCED_SERVICES" ]; then
    # shellcheck disable=SC2086
    compose stop $QUIESCED_SERVICES
  fi
}

# Restarts, in the required order, whatever quiesce_now stopped this run:
# geoserver -> (healthy) -> django -> (healthy) -> (prod only) web -> nginx.
# Clears $QUIESCED_SERVICES on completion.
restart_quiesced() {
  if was_quiesced geoserver; then
    log "Restarting geoserver…"
    compose start geoserver
    wait_ready geoserver
  fi
  if was_quiesced django; then
    log "Restarting django…"
    compose start django
    wait_ready django
  fi
  if [ "$ENV" = "prod" ]; then
    if was_quiesced web; then compose start web && wait_ready web; fi
    if was_quiesced nginx; then compose start nginx && wait_ready nginx; fi
  fi
  QUIESCED_SERVICES=""
}

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
# One psql -tA call against $PG_DATABASE as $PG_SUPERUSER, trailing \r stripped.
psql_query() {
  compose exec -T db psql -tA -U "$PG_SUPERUSER" -d "$PG_DATABASE" -c "$1" | tr -d '\r'
}

# Best-effort GeoServer layer count via the REST API (called while geoserver
# is still up, before quiesce). Prints nothing on any failure — it's
# optional. Credentials go through a curl config file on stdin (-K -) rather
# than -u, so they don't show up in the container's process list.
geoserver_layer_count() {
  printf 'user = "%s:%s"\n' "${GEOSERVER_ADMIN_USER}" "${GEOSERVER_ADMIN_PASSWORD}" \
    | compose exec -T geoserver curl -sf -K - "http://localhost:8080/geoserver/rest/layers.json" 2>/dev/null \
    | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    layers = (d.get("layers") or {}).get("layer") or []
    print(len(layers))
except Exception:
    pass
' 2>/dev/null || true
}

# Writes manifest.json atomically (tmp -> mv). Reads its fields from the
# M_* environment variables the caller sets up.
write_manifest() {
  local snapshot_dir="$1" tmp
  tmp="${snapshot_dir}/manifest.json.tmp"
  python3 - "$tmp" <<'PY'
import json, os, sys

def opt_int(key):
    v = os.environ.get(key, "")
    return int(v) if v.strip() else None

def opt_str(key):
    v = os.environ.get(key, "")
    return v if v.strip() else None

env = os.environ
manifest = {
    "snapshot_id": env["M_SNAPSHOT_ID"],
    "kind": env["M_KIND"],
    "created_at_utc": env["M_CREATED_AT"],
    "env": env["M_ENV"],
    "label": opt_str("M_LABEL"),
    "git_sha": env["M_GIT_SHA"],
    "geoserver_version": env["M_GEOSERVER_VERSION"],
    "geoserver_image": env["M_GEOSERVER_IMAGE"],
    "postgres": {
        "database": env["M_PG_DATABASE"],
        "dump_format": "custom",
        "server_version": opt_str("M_SERVER_VERSION"),
        "postgis_version": opt_str("M_POSTGIS_VERSION"),
        "migration_head": {
            "app": opt_str("M_MIGRATION_APP"),
            "name": opt_str("M_MIGRATION_NAME"),
        },
        "row_counts": {
            "django_migrations": opt_int("M_ROW_COUNT_MIGRATIONS"),
        },
    },
    "geoserver": {
        "data_dir": "/geoserver_data/data",
        "layer_count": opt_int("M_LAYER_COUNT"),
    },
    "artifacts": {
        "postgres.dump": {
            "sha256": env["M_DUMP_SHA256"],
            "bytes": int(env["M_DUMP_BYTES"]),
        },
        "geoserver_data.tar.gz": {
            "sha256": env["M_TAR_SHA256"],
            "bytes": int(env["M_TAR_BYTES"]),
        },
    },
    "tooling_version": env["M_TOOLING_VERSION"],
}

with open(sys.argv[1], "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY
  mv -f "$tmp" "${snapshot_dir}/manifest.json"
}

# Free bytes available on the filesystem holding $1 (creates the dir first
# if missing, so a not-yet-existing backups/ can still be statted).
free_bytes_for() {
  mkdir -p "$1"
  df -Pk "$1" | awk 'NR==2 {print $4 * 1024}'
}

# Disk-space preflight for create (§9): require at least as much free space
# under backups/ as the last snapshot's geoserver_data.tar.gz + postgres.dump
# together. No prior snapshot -> nothing to compare against, skip silently.
check_disk_space() {
  local dir last_tar last_dump needed_bytes free
  # IFS= read -r (not `for dir in $(...)`) so a snapshot dir name containing
  # spaces (e.g. from an unquoted-looking --label) isn't word-split apart.
  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    last_tar="${dir}geoserver_data.tar.gz"
    last_dump="${dir}postgres.dump"
    [ -f "$last_tar" ] && [ -f "$last_dump" ] || continue

    needed_bytes=$(( $(file_bytes "$last_tar") + $(file_bytes "$last_dump") ))
    free="$(free_bytes_for "$BACKUPS_DIR")"
    if [ "$free" -lt "$needed_bytes" ]; then
      die "snapshot: not enough free space under ${BACKUPS_DIR} (need ~${needed_bytes} bytes, have ${free}). Aborting before writing any artifacts."
    fi
    return 0
  done < <(ls -1dt "${BACKUPS_DIR}"/*/ 2>/dev/null || true)
  # No prior snapshot with both artifacts present -> nothing to compare
  # against, skip silently.
  return 0
}

mark_suspect() {
  local snapshot_dir="$1" reason="$2"
  {
    printf 'reason: %s\n' "$reason"
    printf 'flagged_at_utc: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "${snapshot_dir}/suspect.flag"
  warn "suspect: ${reason}"
}

# Runs "$@" (from $3 onward — a real argv, no shell re-parsing) with stderr
# captured to a shared temp file; on failure, marks $1 suspect with
# "$2: <captured stderr>" and returns 1. Used by run_verify_checks so each
# §6.1 check is a single call. Takes argv rather than a command string
# specifically so a snapshot id/label containing shell metacharacters (e.g.
# a stray quote in --label) can't be reinterpreted as shell syntax.
run_check() {
  local snapshot_dir="$1" label="$2" errfile="${1}/.verify.err"
  shift 2
  if ! "$@" >/dev/null 2>"$errfile"; then
    mark_suspect "$snapshot_dir" "${label}: $(cat "$errfile" 2>/dev/null)"
    rm -f "$errfile"
    return 1
  fi
  rm -f "$errfile"
}

# ---------------------------------------------------------------------------
# Lightweight verify (§6.1) — used standalone (`verify` subcommand) and as
# create step 8. Read-only checks against backups/<id>/. On any failure,
# writes suspect.flag with the reason and returns non-zero; the caller
# decides what that means (non-zero exit for standalone, "suspect" summary
# for create).
# ---------------------------------------------------------------------------
run_verify_checks() {
  local snapshot_dir="$1" manifest="${1}/manifest.json"

  [ -f "$manifest" ] || { mark_suspect "$snapshot_dir" "manifest.json missing"; return 1; }

  # --- Artifact integrity: sha256sum of each artifact == manifest value ---
  local artifact expected actual
  for artifact in postgres.dump geoserver_data.tar.gz; do
    local path="${snapshot_dir}/${artifact}"
    if [ ! -f "$path" ]; then
      mark_suspect "$snapshot_dir" "missing artifact: ${artifact}"
      return 1
    fi
    expected="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['artifacts']['${artifact}']['sha256'])" "$manifest" 2>/dev/null || true)"
    [ -n "$expected" ] || { mark_suspect "$snapshot_dir" "manifest missing sha256 for ${artifact}"; return 1; }
    actual="$(sha256_of "$path")"
    if [ "$actual" != "$expected" ]; then
      mark_suspect "$snapshot_dir" "checksum mismatch for ${artifact} (expected ${expected}, got ${actual})"
      return 1
    fi
  done

  # --- Dump openability: pg_restore -l lists a TOC ------------------------
  run_check "$snapshot_dir" "pg_restore -l failed on postgres.dump" \
    pg_restore -l "${snapshot_dir}/postgres.dump" || return 1

  # --- Tar integrity ------------------------------------------------------
  run_check "$snapshot_dir" "tar tzf failed on geoserver_data.tar.gz" \
    tar tzf "${snapshot_dir}/geoserver_data.tar.gz" || return 1

  # A previous failed run may have left a stale flag around; a clean pass
  # clears it.
  rm -f "${snapshot_dir}/suspect.flag"
  return 0
}

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
  do_snapshot manual "$label"
}

# Does the actual snapshot work: preflight, metadata capture, quiesce, dump,
# tar, unquiesce, manifest, lightweight verify. Shared by `create` (kind=manual)
# and `restore`'s mandatory pre-restore safety snapshot (kind=safety, Q9) — the
# caller must already hold the lock and have called resolve_env. Sets
# LAST_SNAPSHOT_ID/LAST_SNAPSHOT_DIR on success; returns non-zero if the
# snapshot ends up suspect (verify failed) — artifacts are still on disk.
do_snapshot() {
  local kind="$1" label="$2"

  # --- Preflight -------------------------------------------------------
  is_ready_now db || die "${kind}: db container is not up/healthy. Aborting."
  check_disk_space

  # --- snapshot_id -------------------------------------------------------
  local git_sha
  git_sha="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  git -C "$REPO_ROOT" diff --quiet 2>/dev/null || git_sha="${git_sha}-dirty"

  local created_at snapshot_id snapshot_dir log_file
  created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  snapshot_id="$(date -u +%Y%m%dT%H%M%SZ)_${git_sha}"
  [ -n "$label" ] && snapshot_id="${snapshot_id}_${label}"
  snapshot_dir="${BACKUPS_DIR}/${snapshot_id}"
  log_file="${snapshot_dir}/snapshot.log"

  mkdir -p "$snapshot_dir"
  # Scope the tee redirection to this function — a caller that keeps running
  # after this (restore, after its pre-restore safety snapshot) must get its
  # own stdout/stderr back, not this snapshot's log file.
  exec 3>&1 4>&2
  exec > >(tee -a "$log_file") 2> >(tee -a "$log_file" >&2)

  log "Creating snapshot ${snapshot_id} (env=${ENV}, kind=${kind})…"

  # --- Metadata while db is up, before quiesce --------------------------
  local migrations_table="${PG_SCHEMA_API}.django_migrations"
  local psql_head server_version postgis_version migration_app migration_name row_count_migrations layer_count
  psql_head="$(psql_query "SELECT app,name FROM ${migrations_table} ORDER BY id DESC LIMIT 1")"
  migration_app="${psql_head%%|*}"
  migration_name="${psql_head#*|}"
  server_version="$(psql_query "SELECT version()")"
  postgis_version="$(psql_query "SELECT postgis_full_version()")"
  row_count_migrations="$(psql_query "SELECT count(*) FROM ${migrations_table}")"
  layer_count="$(geoserver_layer_count)"

  local gs_cid geoserver_image
  gs_cid="$(compose ps -aq geoserver)"
  [ -n "$gs_cid" ] || die "${kind}: geoserver container not found."
  geoserver_image="$(docker inspect -f '{{.Config.Image}}' "$gs_cid")"

  # --- Quiesce -------------------------------------------------------------
  quiesce_now

  # --- Postgres dump --------------------------------------------------
  log "Dumping Postgres…"
  compose exec -T -e PGPASSWORD="$PG_SUPERPASS" db \
    pg_dump -Fc -U "$PG_SUPERUSER" -d "$PG_DATABASE" > "${snapshot_dir}/postgres.dump"

  # --- GeoServer volume tar (geoserver stopped -> consistent) -----------
  log "Archiving geoserver_data…"
  docker run --rm --volumes-from "$gs_cid" -v "${snapshot_dir}:/backup" alpine:3.20 \
    tar czf /backup/geoserver_data.tar.gz -C /geoserver_data/data .

  # --- Unquiesce in order: geoserver -> (healthy) -> django -> (prod) web nginx.
  restart_quiesced

  # --- Manifest ------------------------------------------------------
  local dump_sha256 dump_bytes tar_sha256 tar_bytes
  dump_sha256="$(sha256_of "${snapshot_dir}/postgres.dump")"
  dump_bytes="$(file_bytes "${snapshot_dir}/postgres.dump")"
  tar_sha256="$(sha256_of "${snapshot_dir}/geoserver_data.tar.gz")"
  tar_bytes="$(file_bytes "${snapshot_dir}/geoserver_data.tar.gz")"

  M_SNAPSHOT_ID="$snapshot_id" \
  M_KIND="$kind" \
  M_CREATED_AT="$created_at" \
  M_ENV="$ENV" \
  M_LABEL="$label" \
  M_GIT_SHA="$git_sha" \
  M_GEOSERVER_VERSION="$GEOSERVER_VERSION" \
  M_GEOSERVER_IMAGE="$geoserver_image" \
  M_PG_DATABASE="$PG_DATABASE" \
  M_SERVER_VERSION="$server_version" \
  M_POSTGIS_VERSION="$postgis_version" \
  M_MIGRATION_APP="$migration_app" \
  M_MIGRATION_NAME="$migration_name" \
  M_ROW_COUNT_MIGRATIONS="$row_count_migrations" \
  M_LAYER_COUNT="$layer_count" \
  M_DUMP_SHA256="$dump_sha256" \
  M_DUMP_BYTES="$dump_bytes" \
  M_TAR_SHA256="$tar_sha256" \
  M_TAR_BYTES="$tar_bytes" \
  M_TOOLING_VERSION="$TOOLING_VERSION" \
    write_manifest "$snapshot_dir"

  # --- Step 8: lightweight verify (§6.1, §9) ------------------------------
  # A failure here does not undo the artifacts already written — it marks
  # them suspect so `list`/callers know not to trust this snapshot.
  local result=0
  if run_verify_checks "$snapshot_dir"; then
    printf '✅ snapshot %s hazır\n' "$snapshot_id"
  else
    printf '⚠️  snapshot %s SUSPECT — see %s/suspect.flag\n' "$snapshot_id" "$snapshot_dir"
    result=1
  fi

  # Restore the caller's original stdout/stderr (see the exec above).
  exec 1>&3 2>&4
  exec 3>&- 4>&-

  LAST_SNAPSHOT_ID="$snapshot_id"
  LAST_SNAPSHOT_DIR="$snapshot_dir"
  return "$result"
}

# Prompts "$2 [y/N]" and dies if the answer isn't y/yes, unless $1 ("yes"
# flag, i.e. --yes/YES=1) is "1", in which case it returns immediately.
confirm_or_abort() {
  local yes="$1" prompt="$2" ans=""
  [ "$yes" = "1" ] && return 0
  read -r -p "${prompt} [y/N] " ans || true
  case "$ans" in
    y|Y|yes|YES) return 0 ;;
    *) die "restore: aborted by user." ;;
  esac
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
  # Selective restore is ticket 06 — keep the flag recognized (so the Makefile
  # ONLY= plumbing doesn't blow up) but reject it explicitly rather than
  # silently ignoring it.
  [ -z "$only" ] || die "restore: --only is not yet implemented (ticket 06). Run restore without --only to restore both artifacts."

  resolve_env
  acquire_lock

  local snapshot_dir="${BACKUPS_DIR}/${id}"
  local manifest="${snapshot_dir}/manifest.json"
  [ -d "$snapshot_dir" ] || die "restore: no such snapshot '${id}' (looked in ${snapshot_dir})."

  is_ready_now db || die "restore: db container is not up/healthy. Aborting."

  # --- Preflight: checksum verify — a corrupt artifact is never restored ---
  log "Verifying snapshot ${id} before restore…"
  run_verify_checks "$snapshot_dir" \
    || die "restore: snapshot ${id} is SUSPECT (corrupt/missing artifact) — aborting before any destructive step. See ${snapshot_dir}/suspect.flag"

  # --- Version/git_sha guards (warn + explicit confirm, does not abort) ---
  local m_geoserver_version m_git_sha active_git_sha manifest_fields
  manifest_fields="$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get('geoserver_version') or '')
print(d.get('git_sha') or '')
" "$manifest")"
  m_geoserver_version="$(sed -n '1p' <<<"$manifest_fields")"
  m_git_sha="$(sed -n '2p' <<<"$manifest_fields")"
  active_git_sha="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  git -C "$REPO_ROOT" diff --quiet 2>/dev/null || active_git_sha="${active_git_sha}-dirty"

  if [ -n "$m_geoserver_version" ] && [ "$m_geoserver_version" != "${GEOSERVER_VERSION:-}" ]; then
    warn "⚠️⚠️  GEOSERVER_VERSION MISMATCH: snapshot=${m_geoserver_version} active=${GEOSERVER_VERSION:-<unset>}"
    warn "Restoring geoserver_data under a mismatched GeoServer image/version is exactly the config/image incompatibility that caused the lost-Shapefile incident."
    confirm_or_abort "$yes" "Continue restoring ${id} despite the GEOSERVER_VERSION mismatch?"
  fi

  if [ -n "$m_git_sha" ] && [ "$m_git_sha" != "$active_git_sha" ]; then
    warn "git_sha mismatch: snapshot=${m_git_sha} active=${active_git_sha}"
    warn "entrypoint.sh/entrypoint.prod.sh run 'migrate --noinput' on every django start — if the checked-out code is ahead of the snapshot, django will silently re-apply those migrations against the restored DB right after restore."
    confirm_or_abort "$yes" "Continue restoring ${id} despite the git_sha mismatch?"
  fi

  confirm_or_abort "$yes" "This is DESTRUCTIVE: ${id} will be restored, overwriting the current db and geoserver_data. Continue?"

  # --- Pre-restore safety snapshot (Q9) — never restore without one -------
  log "Taking pre-restore safety snapshot before restoring ${id}…"
  do_snapshot safety pre-restore-safety \
    || die "restore: pre-restore safety snapshot failed or is suspect — aborting restore without a safety net."
  local safety_id="$LAST_SNAPSHOT_ID"
  log "Pre-restore safety snapshot: ${safety_id}"

  # --- Quiesce (db stays up — pg_restore writes to a live db) -------------
  quiesce_now

  # --- Postgres restore (fresh-DB method — avoids --clean cascade fragility) ---
  log "Recreating ${PG_DATABASE}…"
  compose exec -T -e PGPASSWORD="$PG_SUPERPASS" db \
    psql -U "$PG_SUPERUSER" -d postgres -v ON_ERROR_STOP=1 <<SQL
DROP DATABASE IF EXISTS "$PG_DATABASE" WITH (FORCE);
CREATE DATABASE "$PG_DATABASE" OWNER "$PG_SUPERUSER";
SQL

  log "Restoring Postgres from ${id}/postgres.dump…"
  # No --exit-on-error: PostGIS extension notices on a fresh DB are normal.
  # No --no-owner: a superuser connection already applies the dump's
  # ownership/ACLs correctly.
  if ! compose exec -T -e PGPASSWORD="$PG_SUPERPASS" db \
      pg_restore -U "$PG_SUPERUSER" -d "$PG_DATABASE" < "${snapshot_dir}/postgres.dump"; then
    warn "pg_restore reported errors — often normal (PostGIS extension notices); review the output above."
  fi

  # --- GeoServer volume restore (geoserver stopped) ------------------------
  log "Restoring geoserver_data from ${id}/geoserver_data.tar.gz…"
  local gs_cid
  gs_cid="$(compose ps -aq geoserver)"
  [ -n "$gs_cid" ] || die "restore: geoserver container not found."
  docker run --rm --volumes-from "$gs_cid" -v "${snapshot_dir}:/backup" alpine:3.20 sh -c '
    find /geoserver_data/data -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    tar xzf /backup/geoserver_data.tar.gz -C /geoserver_data/data'

  # --- Restart in order: geoserver -> (healthy) -> django -> (prod) web nginx ---
  restart_quiesced

  # --- Post-restore verify (§6.1) ------------------------------------------
  log "Running post-restore verify (geoengine_smoke_test)…"
  if compose exec -T django uv run python manage.py geoengine_smoke_test; then
    printf '✅ restore %s tamamlandı, verify OK (safety snapshot: %s)\n' "$id" "$safety_id"
  else
    printf '⚠️ restore tamam ama verify FAIL — safety snapshot: %s\n' "$safety_id"
    return 1
  fi
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
  local snapshot_dir="${BACKUPS_DIR}/${id}"
  [ -d "$snapshot_dir" ] || die "verify: no such snapshot '${id}' (looked in ${snapshot_dir})."

  if run_verify_checks "$snapshot_dir"; then
    printf '✅ %s: artifact checksums OK, dump TOC OK, tar OK\n' "$id"
  else
    die "verify: ${id} is SUSPECT — see ${snapshot_dir}/suspect.flag"
  fi
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
