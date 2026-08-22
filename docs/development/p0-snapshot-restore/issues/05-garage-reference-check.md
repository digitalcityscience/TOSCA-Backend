# 05 — Garage warning-only reference check in post-restore verify (§6.2, Q11)

**What to build:** After a restore, tell the operator whether the restored database's media
references still resolve in Garage — as a **warning only**. It reports `N references checked, M
missing` and never blocks or fails the restore (full Garage backup/versioning is explicitly out of
P0 scope; this is just a heads-up that some media may be absent).

**Blocked by:** 04.

**Status:** ready-for-agent

- [x] Walk the DB media references (originals + derivatives prefixes) and issue a `HEAD` against
      Garage for each, building on the existing `scripts/list_media_buckets.py` / `scripts/garage_e2e.py`
      infrastructure rather than new S3 plumbing. Implemented as `core.garage_reference_check` +
      `manage.py check_garage_references`, walking `MediaAsset.storage_path` and
      `GeoStory.hero_image` via `storages[alias].exists()` (same `storages[alias]` pattern as
      `list_media_buckets.py`). Derivative images (`core.image_derivatives`) are a lazily-generated,
      content-addressed cache keyed off the original — nothing in the DB references a specific
      derivative key and a missing one regenerates transparently on next request, so they're
      intentionally excluded from the "missing" count; only DB-referenced originals are checked.
- [x] Output `N references checked, M missing` and list the missing keys.
- [x] **Warning-only:** missing references are reported but the restore is **not** blocked and its
      exit status is unaffected — this check can never turn a successful restore into a failure.
      The command itself never raises (a lookup error counts as missing, not a crash), and
      `snapshot.sh` calls it after `geoengine_smoke_test` with its own exit code kept out of the
      restore's return value.
- [x] Invoked as part of the 04 post-restore verify step (after `geoengine_smoke_test`).

**Verify:** on `ENV=dev`, a restore prints the `N checked, M missing` line; deliberately deleting a
referenced object from Garage makes it appear in the missing list **without** changing the restore's
success/exit status. **Rollback risk:** low (read-only `HEAD` probes; non-blocking).
