# 07 — Full restore rehearsal + docs closeout (§6.3, §10 DoD)

**What to build:** The one real guarantee against the "backup taken but can't be restored" trap: a
full restore rehearsal against local `ENV=dev`, reproducing and healing the lost-Shapefile scenario
by eye. Plus the user-facing docs and the final Definition-of-Done checkout for the whole P0 feature.

**Blocked by:** 04, 05.

**Status:** ready-for-agent

- [ ] **Full restore rehearsal (§6.3):** snapshot a known-good local `ENV=dev` state, **delete a
      shapefile-backed GeoServer layer** (the lost-Shapefile scenario), then
      `make restore SNAPSHOT=<id> ENV=dev` and confirm by eye that the layer/map is back — the
      exact failure this feature exists to fix.
- [ ] Document this rehearsal as the pre-upgrade ritual: run it **at least periodically and before
      every GeoServer upgrade** (local and prod share one mechanism, so the local rehearsal validates
      prod too).
- [ ] **Docs:** add snapshot/restore usage to `README` and to `Makefile help` — `make snapshot`,
      `make restore SNAPSHOT=<id>`, `make snapshots`, `ENV`/`LABEL`/`YES`/`ONLY` knobs, and the
      version-mismatch behavior.
- [ ] **DoD checkout (§10):** confirm every box — snapshot on dev + prod produces dump+tar+manifest
      with passing lightweight verify and downtime only during dump+tar; restore takes a safety
      snapshot, restores both, restarts in order, smoke test passes; manifest carries
      `git_sha` + `geoserver_version` and restore warns on mismatch; Garage check is warning-only;
      `backups/` is gitignored.

**Verify:** the deleted shapefile layer renders again after restore; `make help` and README show the
commands; the §10 DoD checklist is fully ticked. **Rollback risk:** none (rehearsal + docs; no
production code paths changed).
