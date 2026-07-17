# Upstream Hermes compatibility

HCA depends on a small set of load-bearing Hermes internals (the kanban
`dispatch_once` scheduler, the `Task` row shape, the concrete worker launch
env/argv, and the plugin subagent hooks). Those are not a public, versioned
API. Instead of trusting a version string, HCA **probes the installed
`hermes_cli` module** for the capabilities it needs and fails closed with an
actionable message when a required seam is missing or a foreign dispatcher
could own its board.

The live, machine-readable version of this document is produced by
`hca doctor --json` (the `compat` block) and by
`hca.hermes_compat.compatibility_report()`.

## Provenance (reconciled)

The 2026-07-12 audit recorded installed upstream source commit `7b5ba205`.
That is **not** the stable release tag and was a snapshot of whatever main was
checked out at audit time. The provenance is now recorded from three distinct
sources so they are never conflated:

| Field | Value | Meaning |
|---|---|---|
| Stable release semver | `0.18.2` | Verified HCA contract lane |
| Stable release calver | `2026.7.7.2` | Same release, calendar tag |
| Stable release tag commit | `9de9c25f620ff7f1ce0fd5457d596052d5159596` | The release the lane pins to |
| Installed upstream commit | `f8ddf4fd` | The main commit this install was built from (advisory) |
| Prior audit source commit | `7b5ba205` | Historical snapshot — superseded, do not treat as the tag |

`hca doctor --json` reports the *installed* semver/calver/upstream commit
live; the stable release tag is the fixed target the `stable` lane verifies
against.

## Supported lanes

Lane is capability-driven, with the version as metadata:

- **stable** — every required capability is present **and** the installed
  version is an explicitly verified release (`0.18.2` / `2026.7.7.2`). This is
  the required contract lane.
- **edge** — every required capability is present but the version is not a
  verified release (e.g. a fresh `main`). Advisory: it may work, but it can
  drift without notice.
- **unsupported** — a required capability is missing. HCA fails closed before
  any claim or spawn with a precise capability error.

## Required capability surface

Probed by `hca.hermes_compat.probe_capabilities()`:

| Group | Required members |
|---|---|
| `dispatch_once` params | `spawn_fn`, `board`, `max_spawn`, `max_in_progress`, `dry_run` |
| `dispatch_once` params (optional) | `max_in_progress_per_profile`, `default_assignee`, `stale_timeout_seconds` |
| `DispatchResult` fields | `reclaimed`, `promoted`, `spawned`, `crashed`, `skipped_nonspawnable` |
| `Task` fields | `id`, `assignee`, `current_run_id`, `claim_lock`, `workspace_path` |
| kanban_db helpers | `dispatch_once`, `kanban_db_path`, `workspaces_root` |
| profile helpers | `normalize_profile_name`, `resolve_profile_env` |

Absence of an *optional* dispatch param downgrades a feature (e.g. the
per-profile concurrency cap), not the whole lane. Absence of any *required*
member makes the install `unsupported`.

### `current_run_id` is the run-ownership key

Upstream exposes the integer active run id as `Task.current_run_id`. HCA maps
it exactly and refuses to spawn a worker for a claimed task without an integer
run id (see `hca.worker_launch.build_worker_launch_spec`). The earlier
guessing among `active_run_id` / `run_id` / `claim_run_id` is removed.

## Worker launch contract

`hca.worker_launch.WorkerLaunchSpec` mirrors the env/argv assembled by
`_default_spawn` **without importing it** (HCA launches workers inside durable
tmux slots, not fire-and-forget subprocesses):

- env: `HERMES_HOME`, `HERMES_TENANT`, `HERMES_KANBAN_TASK`,
  `HERMES_KANBAN_WORKSPACE`, `TERMINAL_CWD` (absolute existing dir only),
  `HERMES_KANBAN_BRANCH`, `HERMES_KANBAN_RUN_ID` (integer), `HERMES_KANBAN_CLAIM_LOCK`,
  `HERMES_KANBAN_GOAL_MODE` / `HERMES_KANBAN_GOAL_MAX_TURNS`, `TERMINAL_TIMEOUT`,
  `TERMINAL_MAX_FOREGROUND_TIMEOUT`, `HERMES_KANBAN_DB`,
  `HERMES_KANBAN_WORKSPACES_ROOT`, `HERMES_KANBAN_BOARD`, `HERMES_PROFILE`.
- argv: `hermes -p <profile> --cli --accept-hooks [--skills X]* [-m MODEL]
  [--toolsets a,b] chat -q "work kanban task <id>" [-Q]`.

A live drift guard (`test_worker_launch_contract.py`) asserts every emitted
env key still appears in the installed `_default_spawn` source, so an upstream
rename fails a test before it fails a fleet.

HCA additionally removes all inherited `HERMES_SESSION_*` identity keys from
the worker command environment and the retained tmux server environment. This
prevents a Telegram/Discord/gateway session from being inherited by an
otherwise isolated worker. Optional-plugin import diagnostics are captured only
when they match known discovery/dependency warnings and are surfaced as
structured doctor checks; unrelated stdout/stderr is never globally suppressed.

## Sole-dispatcher ownership

For an HCA-owned board, the HCA supervisor must be the sole dispatcher. HCA
probes the live gateway pid (`gateway.status.get_running_pid`) and the
`kanban.dispatch_in_gateway` config flag. If a gateway is live **and** that
flag is on, the two dispatchers race to claim the same tasks; HCA fails closed
(`hermes.sole_dispatcher` doctor check, and `dispatch_tick` aborts before any
claim). Remediation: set `dispatch_in_gateway: false` in the participating
profile(s) and restart the gateway, or stop the gateway.

## Subagent hook correlation

The plugin subagent hooks carry these payload keys:

- `subagent_start`: `parent_session_id`, `parent_turn_id`, `parent_subagent_id`,
  `child_session_id`, `child_subagent_id`, `child_role`, `child_goal`.
- `subagent_stop`: `parent_session_id`, `parent_turn_id`, `child_session_id`,
  `child_role`, `child_summary`, `child_status`, `duration_ms`.

**`subagent_stop` does not carry `child_subagent_id`**, so the durable
start/stop correlation key is **`child_session_id`**. (This corrects the
earlier plugin, which read a non-existent `subagent_id` kwarg and released the
oldest lease.)

## Re-verifying before release

`hca doctor --json` regenerates the `compat` block from the live install.
Contract tests (`tests/contract/`) gate the probe logic deterministically
(fakes) and, when hermes is on PATH, assert the real install is a supported
lane. Re-run them whenever the installed Hermes changes.
