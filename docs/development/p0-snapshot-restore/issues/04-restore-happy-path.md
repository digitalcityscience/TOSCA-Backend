# 04 — `restore`: happy path with pre-restore safety net (§5)

**What to build:** `make restore SNAPSHOT=<id> [ENV=dev|prod] [YES=1]` takes the system back to a
snapshot end-to-end: it verifies the snapshot, takes an automatic **pre-restore safety snapshot**,
restores the database into the running `db` and the `geoserver_data` volume, restarts services in the
correct order, and runs a post-restore smoke test. `db` never stops at any stage — restore = writing
to a live `db` with `pg_restore`.

**Blocked by:** 02, 03.

**Status:** ready-for-agent

- [x] **Preflight:** `manifest.json` exists and artifact sha256s match (reuse the 03 verify → **abort
      on mismatch**, a corrupt artifact is never restored). Compare `manifest.geoserver_version` vs
      active `$GEOSERVER_VERSION` → on mismatch a **big warning + explicit confirm** (config/image
      mismatch = second lost-Shapefile risk). Compare `manifest.git_sha` vs active
      `git rev-parse --short HEAD` (+ `-dirty` suffix) → on mismatch a **warning + explicit confirm**
      (does not abort): `entrypoint.sh` / `entrypoint.prod.sh` both run `manage.py migrate --noinput`
      on every django start, so if the checked-out code is ahead of the snapshot, restarting django
      after restore silently re-applies those migrations against the restored DB and partially undoes
      the rollback. Interactive "this is destructive, `<id>` will be restored" confirm, skipped by
      `--yes` / `YES=1`.
- [x] **Pre-restore safety snapshot (Q9):** run `create` with `LABEL=pre-restore-safety`,
      `kind=safety` in its manifest. **If it fails, abort the restore** — never proceed without the
      safety net.
- [x] **Quiesce:** stop `django geoserver` (+`web nginx` on prod). `db` stays up. Register a `trap`
      (alongside the lock-release trap from ticket 01) that restarts the quiesced services if
      `restore` fails mid-flight, so a failed restore doesn't leave the stack down.
- [x] **Postgres restore (fresh-DB method, avoids `--clean` cascade fragility):**
      - Via maintenance db `postgres`: `DROP DATABASE IF EXISTS "$PG_DATABASE" WITH (FORCE)` (PG13+,
        db image is `postgis/postgis:16-3.4` → supported; drops the DB and terminates other
        connections to it in one atomic step, no separate `pg_terminate_backend` needed) +
        `CREATE DATABASE … OWNER $PG_SUPERUSER` (`ON_ERROR_STOP=1`). Global roles live in the
        volume — `DROP DATABASE` does not remove them, and the pg_data volume is **never** deleted.
      - `pg_restore -U $PG_SUPERUSER -d $PG_DATABASE < backups/<id>/postgres.dump` — **no
        `--no-owner` flag**. (The spec previously had `--no-owner=false`, which is not a valid
        pg_restore invocation and fails the command outright; default behavior with a superuser
        connection already applies the dump's ownership/ACLs correctly.) Log errors rather than
        `--exit-on-error` (PostGIS extension notices are normal).
- [x] **GeoServer volume restore** (geoserver stopped): via `--volumes-from $GS_CID`, wipe
      `/geoserver_data/data/*` then `tar xzf` the archive into it (`alpine:3.20`, pinned).
- [x] **Restart in correct order:** `start geoserver` → wait healthy (`/geoserver/web`) →
      `start django` → wait healthy (`/readyz`) → `start web nginx` (prod). "Wait healthy" is the
      same explicit poll loop as ticket 02 — `docker compose start` does not block on healthchecks.
- [x] **Post-restore verify:** run `geoengine_smoke_test`. On failure print
      `⚠️ restore tamam ama verify FAIL — safety snapshot: <id>` (do not silently succeed).

**Verify:** on `ENV=dev`, `make restore SNAPSHOT=<id> YES=1` produces a `pre-restore-safety`
snapshot, restores db + `geoserver_data`, brings services back healthy in order, and
`geoengine_smoke_test` passes. A tampered `GEOSERVER_VERSION` triggers the mismatch warning; a
corrupted artifact aborts before any destructive step. **Rollback risk:** high by nature (destructive)
— mitigated by the mandatory pre-restore safety snapshot and `db` never stopping.
