# 05 — Garage warning-only reference check in post-restore verify (§6.2, Q11)

**What to build:** After a restore, tell the operator whether the restored database's media
references still resolve in Garage — as a **warning only**. It reports `N references checked, M
missing` and never blocks or fails the restore (full Garage backup/versioning is explicitly out of
P0 scope; this is just a heads-up that some media may be absent).

**Blocked by:** 04.

**Status:** ready-for-agent

- [ ] Walk the DB media references (originals + derivatives prefixes) and issue a `HEAD` against
      Garage for each, building on the existing `scripts/list_media_buckets.py` / `scripts/garage_e2e.py`
      infrastructure rather than new S3 plumbing.
- [ ] Output `N referans kontrol edildi, M eksik` and list the missing keys.
- [ ] **Warning-only:** missing references are reported but the restore is **not** blocked and its
      exit status is unaffected — this check can never turn a successful restore into a failure.
- [ ] Invoked as part of the 04 post-restore verify step (after `geoengine_smoke_test`).

**Verify:** on `ENV=dev`, a restore prints the `N checked, M missing` line; deliberately deleting a
referenced object from Garage makes it appear in the missing list **without** changing the restore's
success/exit status. **Rollback risk:** low (read-only `HEAD` probes; non-blocking).
