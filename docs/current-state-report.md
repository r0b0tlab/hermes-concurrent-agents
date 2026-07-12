# Current state (2026-07-12)

## Product

`hermes-concurrent-agents` **v2.0.0a1** — GB10-first control plane (`hca`) for concurrent isolated Hermes sessions on vLLM/SGLang.

## Implemented

- Python package + CLI: init, doctor, up/drain/down, ps/watch, peek/attach/logs, activity/transcript/inspect/explain, plan, bench, task, cluster, dashboard
- Durable tmux slots, state DB, leader lock, drain flag
- Kanban `dispatch_once(spawn_fn=tmux)` adapter
- vLLM + SGLang equal first-class adapters + admission
- Workspaces (worktree policy)
- Subagent budget plugin hook
- GB10 / cluster presets; NVIDIA playbook alignment docs
- CI: Ubuntu primary + macOS compat
- Unit + Hermes contract tests

## Authoritative plan

[docs/plans/2026-07-12-hermes-agent-modernization.md](plans/2026-07-12-hermes-agent-modernization.md)

## Superseded

- Stale INTEGRATION_PLAN → pointer only
- Fixed “3 workers” folklore → measure with `hca bench`
- SGLang “experimental” → first-class
