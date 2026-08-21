# P0 Snapshot / Restore — ticket set

Source spec: `docs/development/p0-snapshot-restore-spec.md`
Generated: 2026-08-21

Single-command, same-host, holistic restore point taken **before deploy/upgrade/migration**
(development/deployment rollback — not disaster recovery). P0 scope = **Postgres (`pg_dump -Fc`)
+ GeoServer `geoserver_data` volume + manifest/verify**. Delivered as `make snapshot` / `make
restore` / `make snapshots` over a thin `scripts/snapshot.sh`, using the existing `which-env`
pattern so local and prod share one mechanism (only `ENV=` and the extra `web nginx` quiesce
differ).

## Branching

**Integration branch: `feature/p0-snapshot-restore`.** Every ticket is done on its own sub-branch
cut **from `feature/p0-snapshot-restore`** (not from `main`), e.g. `feature/p0-snapshot-restore-01-scaffold`,
and merged back into `feature/p0-snapshot-restore`. A ticket that is blocked by another branches off
`feature/p0-snapshot-restore` **after** its blocker has merged in, so it inherits that work. Only the
final integration branch merges to `main`.

## Dependency order

```
01 scaffold ─┬─> 02 create ─┬─> 03 verify ─┬─> 04 restore ─┬─> 05 garage-check ─┬─> 07 rehearsal + docs
             │              │              │               └─> 06 selective-restore
```

| # | Ticket | Blocked by |
|---|--------|-----------|
| 01 | Scaffold: dispatch + Makefile + guardrail plumbing | — |
| 02 | `create`: snapshot happy path | 01 |
| 03 | Lightweight verify + suspect flagging | 02 |
| 04 | `restore`: happy path with pre-restore safety net | 02, 03 |
| 05 | Garage warning-only reference check | 04 |
| 06 | Selective restore (`--only`) | 04 |
| 07 | Full restore rehearsal + docs closeout (DoD) | 04, 05 |

## Two repo constraints every ticket must respect

1. **Postgres init scripts only run on an empty volume.** Global roles (`api_user`, `gs_user`, …),
   schemas and grants are created on first boot by `init001.sh` +
   `reconcile_service_role_passwords.sh`. → Restore **must not delete the pg_data volume**; it
   drops/recreates only the *database*, leaving global roles in place. `db` never stops.
2. **`GEOSERVER_ENABLE_JDBC_CONFIG=false`** → GeoServer catalog/config and file-based layers
   (Shapefile/GeoTIFF) live on the filesystem in `geoserver_data`. This is the root cause of the
   lost-Shapefile incident; only the volume backup brings them back — `sync_geoserver` does not.
