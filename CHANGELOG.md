# Changelog

## Unreleased

### Fixed
- `hca init` now persists the resolved fleet config; bare `hca up` / `doctor` / `watch`
  reload it instead of silently falling back to defaults (wrong socket/model)
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
  `num_running_reqs`, `num_queue_reqs`); launch pack passes `--enable-metrics`

### Changed
- Engine launch packs aligned with current NVIDIA DGX Spark playbooks:
  SGLang `lmsysorg/sglang:latest-cu130` + `--attention-backend flashinfer`
  (stale "experimental on GB10" warning dropped); both engines set the
  Hermes tool-call parser flags and >=64k context by default
- Presets and SOUL templates moved into the package (`src/hca/presets`,
  `src/hca/templates`) so non-editable installs work
- CI runs `ruff check`

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
