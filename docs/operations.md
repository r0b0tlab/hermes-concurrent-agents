# Operations and recovery

## Day 1

Configure Hermes first, then initialize bounded HCA profiles from that source:

```bash
hca init --preset generic-linux --model <served-model-id> --source-profile default
hca doctor
hca run --source-profile default "Complete one bounded verified task"
```

Use a GB10 preset only when its external endpoint already exists. HCA never
starts or replaces a model server as part of this workflow.

## Run lifecycle

```bash
hca run-status <run-id>
hca respond <run-id> <question-id> "answer"
hca collect <run-id>
hca stop <run-id>
```

- `run-status` reports task state, questions, exact ownership, and blockers.
- `respond` answers one open question and resumes only that branch.
- `collect` refuses empty or premature success and emits a deterministic SHA-256
  manifest.
- `stop` persists `stopping`, signals exact owned groups, preserves partial work,
  and ends as `cancelled` rather than success.

## Fleet admission controls

```bash
hca drain         # stop new admission; active work remains supervised
hca drain --clear # admit again after inspection
hca plan --json   # configuration estimate, not a performance result
```

Lower-level `hca up`, `ps`, `watch`, `peek`, `logs`, and `activity` commands are
diagnostics for the same-host fleet. They are not prerequisites for detached
`hca run` when the controller can start normally.

Set concurrency from exact workload evidence. Memory high/low watermarks use
hysteresis; unavailable endpoint metrics retain the conservative configured
sequence ceiling.

## One dispatcher

HCA establishes sole dispatcher ownership before creating any ready Kanban task.
A live Hermes gateway dispatcher targeting the same board is a fail-closed
conflict. Disable gateway dispatch for the HCA board or use a separate board;
do not let two controllers race claims.

## Recovery procedure

1. Read `hca run-status <run-id>` and `hca activity` before changing anything.
2. Verify the recorded PID and start ticks against live ownership.
3. Restart the HCA controller/supervisor; reconciliation is preferred over
   manual SQLite or tmux edits.
4. Answer a persisted question with `hca respond`, or cancel deliberately with
   `hca stop`.
5. Preserve dirty/unmerged worktrees and collect partial evidence.
6. Confirm zero active leases, live exact workers, and HCA-owned panes after a
   terminal run.

Do not manually reset schema markers, delete SQLite WAL files, kill broad process
patterns, drop system caches, or destroy unrelated tmux sessions.

## Common failures

| Symptom | Meaning / action |
|---|---|
| Compatibility preflight code `3` | Install the verified Hermes release or resolve the reported capability drift |
| Sole-dispatcher conflict | Disable gateway dispatch for this board before retrying |
| Admission wait | Inspect memory, disk, slots, active leases, and sequence-credit reason |
| `needs_input` | Answer the exact recorded question; do not edit task rows manually |
| Blocked: no evidence | Worker completed without result/artifact evidence; inspect its upstream run summary/log |
| Remote placement unsupported | Keep workers/Kanban local; configure only the model endpoint through Hermes |
| Terminal task with live worker | Wait for or reconcile exact process cleanup; HCA must not report success yet |

## Upgrade and uninstall

Follow [Migration and uninstall](migration.md). State and profile backups are
owner-only and forward-only migrations fail closed on unknown future versions.
