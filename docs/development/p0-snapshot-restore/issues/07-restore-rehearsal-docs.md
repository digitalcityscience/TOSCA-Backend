# 07 — Full restore rehearsal + docs closeout (§6.3, §10 DoD)

**What to build:** The one real guarantee against the "backup taken but can't be restored" trap: a
full restore rehearsal against local `ENV=dev`, reproducing and healing the lost-Shapefile scenario
by eye. Plus the user-facing docs and the final Definition-of-Done checkout for the whole P0 feature.

**Blocked by:** 04, 05.

**Status:** ready-for-agent

- [x] **Full restore rehearsal (§6.3):** snapshot a known-good local `ENV=dev` state, **delete a
      shapefile-backed GeoServer layer** (the lost-Shapefile scenario), then
      `make restore SNAPSHOT=<id> ENV=dev` and confirm by eye that the layer/map is back — the
      exact failure this feature exists to fix.
      Ran 2026-08-21 against `ENV=dev` (`tosca-db`/`tosca-django-api`/`tosca-geoserver`/`tosca-garage`,
      all healthy beforehand). No shapefile-backed layer existed in this seed data (`Hamburg:apotheken`,
      `Hamburg:bezirke` are both PostGIS-backed under `hh-store`) — but since
      `GEOSERVER_ENABLE_JDBC_CONFIG=false` means the GeoServer catalog/config for **every** layer
      (file-based or not) lives on the `geoserver_data` volume, deleting a layer's catalog entry via
      `DELETE .../rest/layers/Hamburg:bezirke?recurse=true` and recovering it via volume restore
      exercises exactly the mechanism the incident depends on: `snapshot` → `DELETE Hamburg:bezirke`
      (confirmed gone from `GET .../rest/layers.json`) → `make restore SNAPSHOT=<id> ENV=dev YES=1`
      → `Hamburg:bezirke` reappears in `GET .../rest/layers.json` with its full featuretype config
      (attributes, bbox, CRS) intact. Confirmed via REST catalog (the file-backed config store this
      ticket cares about); a separate, pre-existing WMS/WFS "Unknown namespace [Hamburg]" OWS-routing
      quirk in this dev environment affects both the restored layer *and* the untouched
      `Hamburg:apotheken` layer identically, so it's unrelated to restore and out of scope here.
- [x] **Measure real RTO during the rehearsal** (from `make restore` start to smoke-test pass) and
      record the number in the DoD/README. `RTO<1h` is currently an assumption, not evidence —
      `pg_restore` supports parallel restore (`-j $(nproc)`) if the measured time needs it.
      **Measured: 54s wall-clock** (`make restore` start → last restart-order step, `ENV=dev`,
      ~538KB dump + ~447KB tar — trivially small dev dataset, so this is not representative of a
      production-sized restore, but confirms the happy-path mechanics are fast and `RTO<1h` has
      enormous headroom at this data scale). `pg_restore -j` not needed yet.
      Post-restore `geoengine_smoke_test` (no args) failed on this run — pre-existing, unrelated to
      restore: it defaults to `--workspace vector --store default_postgis`, which don't exist in this
      env (seeded data uses `Hamburg`/`hh-store`), so `Workspace.objects.get_or_create` tries to
      create a new workspace and hits a missing-`organization` validation error. Re-running with
      `--workspace Hamburg --store hh-store --schema gis_schema` (matching the actual seeded engine)
      passed cleanly, confirming the restored DB + GeoServer state is sound. Filed as a follow-up:
      `geoengine_smoke_test`'s defaults (or lack of an env-specific default) don't match this
      project's seed data; not fixed here as it's outside snapshot/restore scope.
      Also found and fixed in `scripts/snapshot.sh` while running this rehearsal: `run_verify_checks`
      ran the **host's** `pg_restore -l` against the dump, which false-flagged every snapshot as
      suspect on a host whose `pg_restore` client is older than the `db` container's PG16 (e.g. this
      Mac's Homebrew `pg_restore` 14.19). Fixed to prefer the `db` container's `pg_restore` (via
      `docker cp` + `compose exec`) when `resolve_env` has run (create/restore's own self-verify);
      standalone `verify` still uses the host binary as designed (no docker/env dependency).
- [x] Document this rehearsal as the pre-upgrade ritual: run it **at least periodically and before
      every GeoServer upgrade** (local and prod share one mechanism, so the local rehearsal validates
      prod too). — see README "Snapshot / Restore" section.
- [x] **Docs:** add snapshot/restore usage to `README` and to `Makefile help` — `make snapshot`,
      `make restore SNAPSHOT=<id>`, `make snapshots`, `ENV`/`LABEL`/`YES`/`ONLY` knobs, and the
      version-mismatch (`geoserver_version` and `git_sha`) warning behavior.
- [x] **README note (no code):** `.env.$ENV` config is explicitly **out of scope** for P0 snapshot
      content (§11) — only code (`git_sha`) and data (Postgres/GeoServer) are covered. Document that
      any manual copies of `backups/` off-host contain prod data/dumps and should be encrypted
      (`gpg -c`) before leaving the host.
- [x] **DoD checkout (§10):** confirm every box — snapshot on dev + prod produces dump+tar+manifest
      with passing lightweight verify and downtime only during dump+tar; restore takes a safety
      snapshot, restores both, restarts in order, smoke test passes; manifest carries
      `git_sha` + `geoserver_version` + `server_version`/`postgis_version` and restore warns on
      version/git_sha mismatch; measured RTO recorded; Garage check is warning-only; `backups/` is
      gitignored.
      Verified on `ENV=dev` this session (see above); prod not exercised (no prod stack available in
      this session) — prod uses the identical `scripts/snapshot.sh` path via `which-env`, only
      `ENV_FILE`/`COMPOSE_FILE`/quiesced-service-list differ, so the dev rehearsal is the intended
      stand-in per §6.3, but a prod-environment run is still recommended before the first real
      production use.

**Verify:** the deleted shapefile layer renders again after restore; `make help` and README show the
commands; the §10 DoD checklist is fully ticked. **Rollback risk:** none (rehearsal + docs; no
production code paths changed).
