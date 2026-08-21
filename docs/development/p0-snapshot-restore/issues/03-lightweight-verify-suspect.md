# 03 — Lightweight verify + suspect flagging (§6.1)

**What to build:** A `verify` subcommand that proves a snapshot is restorable-shaped without doing a
full restore, plus its wiring as the final step of `create` so every fresh snapshot is checked. A
snapshot that fails any check is marked `suspect` and is **not** reported as successful. Also add the
pre-create disk-space guard so `create` never half-writes into a full disk.

**Blocked by:** 02.

**Status:** ready-for-agent

- [x] `scripts/snapshot.sh verify --id <snapshot_id>` runs the §6.1 lightweight checks:
      - **Artifact integrity:** `sha256sum` of each artifact == the value in `manifest.json`.
      - **Dump openability:** `pg_restore -l backups/<id>/postgres.dump` lists a TOC (file intact).
      - **Tar integrity:** `tar tzf backups/<id>/geoserver_data.tar.gz >/dev/null`.
- [x] Any failure creates `backups/<id>/suspect.flag` (with the reason) and exits non-zero; a clean
      run prints a concise pass summary.
- [x] Wire verify as **create step 8**: after the manifest is written, run the same checks; on
      failure mark the snapshot `suspect` and print the suspect message instead of `✅`. (§9:
      "any create step fails → mark `suspect.flag`, not counted as successful.")
- [x] **Disk-space preflight for create** (§9): before dumping, check `backups/` has at least the
      size of the last `geoserver_data` + dump; abort with a clear message if not.
- [x] `verify` also usable standalone on any existing snapshot id.

**Verify:** `make snapshot ENV=dev` ends with a passing verify and no `suspect.flag`; manually
corrupting `postgres.dump` (or editing a checksum) and re-running `snapshot.sh verify --id <id>`
produces `suspect.flag` and a non-zero exit. **Rollback risk:** low (read-only checks + a flag file).
