# Migration and uninstall

HCA upgrades preserve user-owned Hermes configuration and HCA state. Do not
delete profiles or state as an upgrade strategy.

## Before upgrading

1. Stop admitting new work and inspect active ownership:

   ```bash
   hca drain
   hca run-status
   ```

2. Collect terminal runs and deliberately stop any run that must not continue:

   ```bash
   hca collect <run-id>
   hca stop <run-id>
   ```

3. Back up the HCA state directory and the selected Hermes source profile using
   your normal owner-only backup process. Never copy credentials into a bug
   report or release artifact.

4. Install the new HCA wheel or checkout, then preview profile changes:

   ```bash
   hca init --dry-run --source-profile <profile> --preset <preset> --model <id>
   ```

5. Run `hca doctor` before admitting new work.

## State schema migration

The current HCA SQLite schema is version `3`; run specifications use schema
version `2`.

Opening HCA state applies ordered forward-only migrations. Before changing an
existing database, HCA creates a WAL-consistent owner-only backup beside it:

```text
hca.sqlite.bak-<time-ns>
```

Each migration runs in its own transaction and is followed by SQLite integrity,
schema-marker, table, and required-column checks. If a step fails, HCA restores
the pre-migration backup and refuses startup. A database created by a newer HCA
schema is rejected; upgrade HCA rather than downgrading or editing the marker.

Do not copy, edit, or restore only `-wal`/`-shm` files. Restore the verified
backup database while HCA processes are stopped.

## Generated Hermes profiles

HCA profiles are derived through `hermes profile create`, then tightened to
role-scoped tools and the source profile's approval policy. Existing profiles
are preserved unless `--force` is explicitly supplied. Before tightening a
profile, HCA writes an owner-only backup:

```text
config.yaml.hca-bak.<time-ns>
```

If profile configuration or `hermes -p <slot> config check` fails, HCA restores
the backup and rejects the slot. HCA never prints or writes literal credential
values into fleet snapshots or result manifests.

## Migrating from legacy cluster configuration

Stable remote agent placement and the `gb10-cluster-*` presets have been
removed. Legacy `control`/`node` role mutations and remote node startup return
preflight code `3` before side effects.

Migrate to a single-host fleet:

1. Keep the Kanban board, HCA controller, workers, state, and workspaces on one
   host.
2. Configure a remote inference endpoint only through the selected Hermes
   profile if required.
3. Reinitialize with `generic-linux`, `gb10-vllm`, or `gb10-sglang`.
4. Run `hca doctor`, then a one-worker canary before enabling concurrency.

See [Remote placement and GB10 clusters](gb10-cluster.md).

## Rollback

Code rollback is safe only when the on-disk schema is not newer than the target
build. If migration itself failed, HCA has already restored and verified the
pre-migration backup. For an operator-directed rollback:

1. Drain and stop HCA-owned workers.
2. Preserve the current database for diagnosis.
3. Restore the matching `hca.sqlite.bak-<time-ns>` as one SQLite database.
4. Restore any corresponding generated-profile config backups.
5. Install the matching HCA version and run `hca doctor` before startup.

Never force a lower `schema_version` value.

## Legacy resolved snapshots

Older `fleet.resolved.json` files serialized backend/metrics URLs and cluster
inventory. On the first compatible read, HCA may use that data for the current
invocation and atomically replaces the file with an owner-only scheduling
snapshot that contains no endpoint, metrics URL, auxiliary endpoint, host, SSH
user, or cluster connection data.

Preset endpoints are reconstructed from package data. Custom values must be
provided at runtime through `--config`, `--endpoint`, `HCA_BACKEND_ENDPOINT`,
`HCA_BACKEND_METRICS_URL`, or `HCA_AUXILIARY_ENDPOINT`. Do not add the literal
value back to HCA state.

## Uninstall

Package uninstall intentionally preserves user data:

```bash
python -m pip uninstall hermes-concurrent-agents
```

That command does not delete HCA state, results, worktrees, generated Hermes
profiles, or source-profile credentials. Before removing data:

1. Reinstall or retain the matching `hca` command long enough to drain, collect,
   and stop owned work.
2. Inspect every path and preserve dirty/unmerged worktrees.
3. Use `hermes profile list` and delete only profiles whose names and ownership
   you have verified as HCA-generated.
4. Delete an HCA state directory only after confirming it contains no required
   result, backup, log, or dirty workspace.

HCA never removes the user's source Hermes profile or credential files as part
of uninstall.
