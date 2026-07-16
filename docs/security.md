# Security model

HCA is an orchestration control plane, not a host sandbox or credential broker.
Its security properties come from narrow ownership, fail-closed compatibility,
least-privilege worker profiles, and explicit operator approval.

## Trust boundaries

- **Hermes Agent:** owns providers, model selection, credentials, ordinary tool
  implementations, sessions, profiles, Kanban task truth, and approvals.
- **HCA controller:** owns one persisted bounded graph, admission, concrete slot
  reservations, exact worker/process identities, HCA-created tmux sessions,
  leases, and deterministic result projection.
- **Workers:** may execute only their assigned task and task-scoped Kanban
  operations. They may not mutate or dispatch unrelated graph work.
- **Operator:** owns installation, source-profile selection, endpoint exposure,
  host access, and destructive lifecycle decisions.

HCA does not claim containment against a malicious process with the same Unix
user privileges. Worktree/profile isolation prevents accidental cross-task
mixing in tested workflows; it is not a kernel security boundary.

## Credentials and endpoints

Generated profiles preserve credential references through Hermes profile
creation. HCA must not copy literal credentials or connection strings into
source, fleet snapshots, state, logs, events, manifests, artifacts, or
summaries. Controller snapshots omit backend and cluster configuration.

Keep model endpoints private to the host or trusted network. Remote inference
remains Hermes configuration; do not encode endpoint credentials in HCA TOML or
command history.

## Approvals and tools

- Generated workers never inherit an approval bypass from a source profile.
- Optional plugins are disabled in worker slots so plugin-provided toolsets
  cannot bypass role allowlists.
- Worker delegation is disabled by default; durable fan-out uses visible Kanban
  children.
- `hca_team_stop` enters the real Hermes human-approval path and also requires
  exact run authorization.
- CLI stop requires an interactive confirmation or explicit `--yes` for
  non-interactive automation.

## Graph and result integrity

The HCA controller is the sole graph owner. Dispatch receives an explicit set of
persisted task IDs. Out-of-graph tasks are denied, blocked from ready dispatch,
and recorded through `run.graph_expansion_denied` without receiving a worker or
lease.

A run cannot report terminal success while a required exact worker remains live,
while declared review is unresolved, or without result/artifact evidence. A
blocked, cancelled, or needs-input run is never projected as success.

## Process ownership and cleanup

Linux worker identity is PID plus `/proc/<pid>/stat` start ticks. HCA signals
only the exact owned process group after rechecking that identity. Cancellation
persists `stopping` before controller termination and uses bounded TERM followed
by identity-checked KILL. Unrelated tmux sessions and processes are outside HCA
cleanup scope.

## Remote placement

Remote agent placement is unsupported and fails before SSH mutation. Read-only
inventory and SSH reachability do not imply task placement support. HCA rejects
NFS SQLite, ad hoc replication, and an HCA-owned distributed task database.
Remote model endpoints remain supported through Hermes profiles.

## Release guards

`scripts/public-safety-check.py` scans tracked and unignored public text for:

- private-key and common production-token shapes;
- credential-bearing URLs and private absolute home paths;
- masked required contract/release commands; and
- remote-placement startup commands in stable product surfaces.

`scripts/release-check.sh` combines that scan with Ruff, tests, generated support
metadata, documentation/link validation, and build verification. These checks
reduce accidental disclosure; they do not replace secret rotation or manual
review.

For vulnerability reporting, see the repository-level [security policy](../SECURITY.md).
