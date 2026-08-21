# 06 — Selective restore (`--only`) (§9, Q1)

**What to build:** Let an operator restore just one artifact of a snapshot when only Postgres or only
GeoServer needs to be rolled back, instead of always restoring both. The safety net stays intact — a
**full** pre-restore safety snapshot is taken regardless of what is being selectively restored.

**Blocked by:** 04.

**Status:** ready-for-agent

- [x] `restore --only postgres` and `restore --only geoserver` flags (surfaced through the Makefile,
      e.g. `ONLY=postgres`); default with neither = restore both (current 04 behavior).
- [x] `--only postgres` runs only the Postgres fresh-DB restore path; `--only geoserver` runs only the
      `geoserver_data` volume wipe+untar. Quiesce/restart still cover the services the chosen artifact
      affects, in the correct order.
- [x] The pre-restore safety snapshot is still taken **in full** (both artifacts) even for a selective
      restore — a partial restore must never be the only thing standing between us and data loss.
- [x] Preflight checksum/version guards from 04 still apply to the selected artifact.
- [x] A selective restore leaves the other artifact (DB or `geoserver_data`) at whatever state it
      was already in, which can now diverge from the snapshot just restored — print a warning that
      DB↔catalog drift is possible after a `--only` restore (this is exactly the kind of mismatch
      that produced the original lost-Shapefile incident, just from the other direction).

**Verify:** on `ENV=dev`, `restore --only geoserver` restores the volume and leaves the database
untouched (row counts unchanged); `restore --only postgres` restores the DB and leaves
`geoserver_data` untouched; both first produce a full `pre-restore-safety` snapshot. **Rollback
risk:** medium (destructive, but narrower scope; full safety snapshot retained).
