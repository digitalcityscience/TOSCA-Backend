# 01 — Scaffold: `snapshot.sh` dispatch + Makefile targets + guardrail plumbing

**What to build:** The thin orchestrator shell and Makefile surface everything else hangs off.
After this ticket a developer can run `make snapshots` and get an (empty) list, and `make snapshot`
/ `make restore` dispatch through `scripts/snapshot.sh` to stubs — no artifacts produced yet. All
the cross-cutting plumbing (env resolution, the compose-call helper, the concurrency lock) lives
here so tickets 02–07 only add business logic.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `scripts/snapshot.sh` with `set -euo pipefail` and a subcommand dispatcher:
      `create | restore | list | verify` (unknown → usage + non-zero exit). `create`, `restore`,
      `verify` may be stubs that echo "not yet implemented"; `list` prints a header + iterates
      `backups/*/manifest.json` summaries (empty is fine).
- [ ] Env resolution matching the existing `which-env` pattern: consume `ENV_FILE`, `COMPOSE_FILE`,
      `ENV` from the caller and source `PG_*`, `GEOSERVER_VERSION`, `S3_*` from `.env.$ENV`. Validate
      `ENV` ∈ {dev, prod} and that `COMPOSE_FILE`/`ENV_FILE` exist; abort otherwise.
- [ ] A single compose helper so every call is
      `docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" …` — no ad-hoc compose invocations
      anywhere in the script.
- [ ] Concurrency lock (`backups/.lock`, §9): a second concurrent `create`/`restore` is refused with
      a clear message; the lock is released on exit (including on error/trap).
- [ ] Makefile targets as **thin shells only** (all logic stays in the script):
      ```make
      snapshot: which-env
      	@ENV_FILE=$(ENV_FILE) COMPOSE_FILE=$(COMPOSE_FILE) ENV=$(ENV) \
      	  scripts/snapshot.sh create --label "$(LABEL)"
      restore: which-env
      	@test -n "$(SNAPSHOT)" || { echo "SNAPSHOT=<id> gerekli"; exit 1; }
      	@ENV_FILE=$(ENV_FILE) COMPOSE_FILE=$(COMPOSE_FILE) ENV=$(ENV) \
      	  scripts/snapshot.sh restore --id "$(SNAPSHOT)" $(if $(YES),--yes,)
      snapshots: which-env
      	@scripts/snapshot.sh list
      ```
- [ ] `snapshot restore snapshots` added to `.PHONY`; usage lines added to the `help` target.
- [ ] `backups/` added to `.gitignore`.

**Verify:** `make snapshots ENV=dev` prints an empty list without error; `make snapshot ENV=dev`
and `make restore SNAPSHOT=x ENV=dev` reach their stubs; a manual second-lock attempt is refused.
**Rollback risk:** low (new files + additive Makefile/gitignore).
