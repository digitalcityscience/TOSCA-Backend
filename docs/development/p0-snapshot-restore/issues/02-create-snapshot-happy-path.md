# 02 — `create`: snapshot happy path (dump + tar + manifest)

**What to build:** `make snapshot [ENV=dev|prod] [LABEL=…]` produces a complete, self-describing
restore point under `backups/<snapshot_id>/`: a `pg_dump -Fc` of the database, a full `tar.gz` of the
`geoserver_data` volume, and a `manifest.json` with metadata + checksums. Downtime is only the
dump+tar window (services quiesced for that window, `db` stays up).

**Blocked by:** 01.

**Status:** ready-for-agent

- [x] **Preflight:** `db` container UP + healthy (abort otherwise); create `backups/<id>/`, tee stdout
      /stderr to `snapshot.log`.
- [x] **`snapshot_id`** = `<UTC>_<git rev-parse --short HEAD>[-dirty][_<LABEL>]`,
      e.g. `20260821T142530Z_6380333_pre-epic12`. Append `-dirty` when
      `git diff --quiet` fails, so uncommitted deploys stay visible instead of
      silently disappearing from the id/manifest.
- [x] **Metadata while db is up (before quiesce):** `migration_head` (last row of
      `django_migrations`), key `row_counts`, and optional GeoServer `layer_count` — via
      `exec -T db psql -tA -U $PG_SUPERUSER -d $PG_DATABASE`.
- [x] **Quiesce:** stop `django geoserver` (dev); on **prod** also stop `web nginx`. `db` stays up so
      `pg_dump` can connect.
- [x] **Postgres dump:** `exec -T -e PGPASSWORD=$PG_SUPERPASS db pg_dump -Fc -U $PG_SUPERUSER -d
      $PG_DATABASE > backups/<id>/postgres.dump`.
- [x] **GeoServer volume tar** (geoserver stopped → consistent): resolve the container with
      `ps -aq geoserver` (stopped container included), then
      `docker run --rm --volumes-from $GS_CID -v "$PWD/backups/<id>:/backup" alpine:3.20
      tar czf /backup/geoserver_data.tar.gz -C /geoserver_data/data .`. Using `--volumes-from`
      (not a volume name) is what makes dev/prod identical — do not hardcode `pg_data`/`geoserver_data`
      volume names. Pin `alpine:3.20` (not floating `alpine:3`) so a snapshot never depends on
      what the registry resolves that tag to at that moment.
- [x] **Unquiesce in correct order:** `start geoserver` → wait healthy → `start django`
      (→ `start web nginx` on prod). "Wait healthy" is an **explicit poll loop**
      (`until [ "$(docker inspect -f '{{.State.Health.Status}}' $CID)" = healthy ]; do sleep 2; done`
      + timeout) — `docker compose start` does **not** block on the healthcheck the way
      `docker compose up --wait` does, and geoserver/django/db all have real healthchecks
      defined in the compose files, so this loop reads a meaningful signal.
- [x] **Error-path unquiesce:** register a `trap` (alongside the existing lock-release trap
      from ticket 01) that restarts whichever services this run quiesced if `create` fails
      mid-flight — a failed snapshot must not leave django/geoserver stopped.
- [x] **Manifest** (`manifest.json`, §3 schema): `snapshot_id`, `kind: "manual"`, `created_at_utc`,
      `env`, `label`, `git_sha` (with `-dirty` suffix when applicable), `geoserver_version`
      (=`$GEOSERVER_VERSION`), `geoserver_image`, `postgres` block (database, `dump_format: custom`,
      `server_version` (`SELECT version()`), `postgis_version` (`SELECT postgis_full_version()`),
      `migration_head`, `row_counts`), `geoserver` block, `artifacts` (sha256 + bytes for each file),
      `tooling_version: "p0-snapshot/1"`. Written atomically (tmp → mv). `git_sha` + `geoserver_version`
      are **mandatory**. `server_version`/`postgis_version` mirror the existing `geoserver_version`
      compatibility check pattern and cost one extra `psql` query while `db` is already up (step
      "Metadata while db is up").
- [x] Success prints `✅ snapshot <id> ready`. (Verify + suspect handling arrives in 03.)

**Verify:** `make snapshot ENV=dev` yields `backups/<id>/` containing `postgres.dump`,
`geoserver_data.tar.gz`, `manifest.json`, `snapshot.log`; services are back up and healthy
afterward; `make snapshots` now lists the new snapshot. **Rollback risk:** low (produces files
under gitignored `backups/`; the quiesce window is the only runtime effect).
