# Changelog

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
- CI: Ubuntu primary (3.11/3.12) + macOS compat
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
