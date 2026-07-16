# Changelog

## Unreleased

### Added
- Goal-to-team product surface: `hca run/run-status/respond/collect/stop` and
  the five Hermes plugin tools (`hca_team_run/status/collect/respond/stop`,
  the last approval-gated) call one shared typed service (`hca.service`).
  Immutable `RunSpec`, finite versioned `RunState` machine, structured
  `Question`s, and a deterministic SHA-256'd `RunResult` manifest that never
  reports cancelled/blocked work as success. Standardized exit codes
  (0/2/3/4/5) and `remediation` on every result; idempotency keys (goal text
  never deduplicates); honest `blocked` when no execution backend is admitted.
  Bundled `hca-operations` skill and `default`/`small`/`reviewed` team
  templates; `docs/running-a-team.md`.
- Executable Hermes compatibility matrix: `hermes_compat` now probes the
  installed `hermes_cli` for the exact capability surface HCA needs
  (dispatch params, `DispatchResult`/`Task` fields, kanban/profile helpers),
  classifies the install as `stable`/`edge`/`unsupported`, and fails closed
  with an actionable message on drift. Reported under `compat` in
  `hca doctor --json`; documented in `docs/upstream-compatibility.md`.
- Sole-dispatcher detection: doctor and the dispatch tick fail closed when a
  live Hermes gateway with `kanban.dispatch_in_gateway: true` could claim the
  HCA board before the supervisor.
- Typed `WorkerLaunchSpec` (`hca.worker_launch`) mirroring the upstream
  `_default_spawn` env/argv contract (integer `current_run_id`, `TERMINAL_CWD`,
  branch/tenant, goal-mode `-Q`, task skills, model override, profile toolsets,
  runtime-derived terminal timeouts) with a live source drift guard.
- Concrete-slot routing (`hca.routing`): logical roles resolve to an eligible
  *free* concrete profile slot with pre-reservation; unknown role/requirement
  hints are unroutable (fail visibly) instead of silently mapped to `coder`.
- Deterministic orchestration acceptance runner plus generic real-HTTP fake
  endpoint coverage, explicit useful-overlap/fan-in evidence, and conservative
  telemetry-disabled admission coverage.
- Generated support matrix, migration/uninstall guide, security model, private
  vulnerability-reporting policy, project/upstream attribution notice, and
  machine-enforced public-source safety scan.
- Required CI lanes for Python 3.11/3.12/3.13 unit/static checks, pinned stable
  Hermes contracts, clean wheel/plugin discovery, generic integration, macOS
  portability smoke, and a clearly advisory latest-Hermes-main drift probe.
  Workflow semantics are validated with `actionlint`, not only a YAML parser.

### Fixed
- Dispatch is reservation-first: the spawn callback makes no admission
  decision and **raises** rather than returning `None` after a claim (which
  upstream records as an invisible stuck `spawned` row). Per-profile cap of 1
  prevents duplicate workers on a concrete slot.
- `Task.current_run_id` is mapped exactly and required (integer) before spawn.
- `hca task swarm --workers` no longer silently ignores the flag — it fails
  visibly (exit 2) and points at fleet-level concurrency.
- `hca task add --repo` binds a git worktree to the real task via Hermes'
  canonical `--workspace worktree:<path>` contract instead of a detached
  `pending-<timestamp>` worktree the task never references.
- `hca init` now persists an owner-only scheduling snapshot; package-preset
  endpoints are reconstructed while custom endpoint/metrics/cluster connection
  data is never retained. Legacy unsafe snapshots are sanitized on first read.
- Plan, init, benchmark, and doctor reports expose endpoint scope rather than
  connection strings; collected manifests omit absolute state paths. `hca init
  --dry-run` no longer creates the state directory.
- Kanban spawn never respawns a busy slot (would kill the running worker and
  violate the live-slot unique index); saturated roles stay queued
- `hca logs` works: worker output is captured via `tmux pipe-pane` into
  `state_dir/logs/<run_id>.log`
- Worker env no longer overrides `HERMES_HOME` (conflicted with `hermes -p <profile>`)
- Generated profile config.yaml follows the documented Hermes schema
  (`model.provider: custom`, `api_mode: chat_completions`, `context_length`)
- vLLM metrics: 0.0 readings no longer treated as missing; prefix-cache hit rate
  computed from hits/queries counters
- SGLang metrics: Prometheus `/metrics` actually parsed (`token_usage`,
  `num_running_reqs`, `num_queue_reqs`)
- Completion projection waits for exact live worker cleanup; successful
  upstream run summaries count as result evidence while blocked summaries stay
  blockers. Worker logs are board/task/run namespaced so board-local integer run
  IDs cannot collide.
- Worker-created tasks outside the persisted HCA graph are excluded from
  dispatch, blocked, and evidenced through `run.graph_expansion_denied` without
  receiving a lease or process.
- Generic work now routes to a bounded first-class `general` role rather than a
  coder persona. Generated workers disable optional plugins and broad skill
  context so role toolsets remain least privilege.

### Changed
- Backend docs now point to operator/NVIDIA-owned serving and require
  model/runtime-specific tool-parser and measured context configuration.
- Presets and SOUL templates moved into the package (`src/hca/presets`,
  `src/hca/templates`) so non-editable installs work
- Runtime metadata declares the actual `pyyaml>=6.0,<7` dependency, modern SPDX
  license metadata, and packages both `LICENSE` and `NOTICE`. Source
  distributions include migration, security, support, and operational docs.
- The local release gate honors an exact `HCA_HERMES_SRC` checkout so pinned
  compatibility verification does not depend on import order or mutable main.
- Stable product documentation now centers `hca run` and distinguishes remote
  inference (Hermes-configured and supported) from remote agent placement
  (unsupported).
- Contract tests against Hermes `0.18.2 / 2026.7.7.2` are mandatory and cannot
  be masked; latest-main compatibility remains a visible advisory job.

### Removed
- HCA-owned vLLM/SGLang Compose/launch assets. HCA consumes existing endpoints;
  it does not install, stop, replace, or own a model server.
- Stable remote-agent startup and the `gb10-cluster-*` presets. Legacy
  control/node mutations and `cluster nodes up` return preflight code `3`
  before SSH, state, profile, graph, or supervisor side effects. Read-only
  inventory/SSH doctor helpers remain explicitly experimental.

## 2.0.0 - 2026-07-12

Complete v2 control plane for GB10 / DGX Spark concurrent Hermes fleets.

### Added
- `hca` CLI: init, doctor, up/drain/down, ps/watch, peek/attach/logs, activity/transcript/inspect/explain, plan, bench, task, cluster, dashboard
- Durable tmux slots (`hca-<fleet>-<role>-NN`), warm idle slots, leader lock
- SQLite control-plane state + activity stream + drain flag
- Hermes Kanban `dispatch_once(spawn_fn=…)` tmux adapter
- Equal first-class **vLLM** and **SGLang** adapters + capacity admission
- Workspaces (git worktree / shared-readonly / none by role)
- Subagent budget plugin hook
- Full concurrency bench harness with knee detection
- Cluster inventory over **passwordless SSH** (NVIDIA playbook-aligned)
- Presets: gb10-vllm, gb10-sglang, gb10-cluster-*, generic-linux
- Docs: architecture, operations, observability, cluster, backends, NVIDIA index, isolation, subagents, benchmarking
- CI: Linux GitHub runners for unit/smoke only; GB10 validation is on-device (`hca doctor` / `bench` / fleet smoke)
- Unit + Hermes contract tests

### Changed
- Product default path is DGX Spark / GB10 (not laptop folklore)
- SGLang is first-class (not experimental)
- Legacy shell scripts deprecated as wrappers → `hca`

### Removed / superseded
- Stale INTEGRATION_PLAN content (pointer only)
- Fixed universal “N=3 workers” guidance

## 1.0.x
- Prior shell-based scaffolding (see git history)
