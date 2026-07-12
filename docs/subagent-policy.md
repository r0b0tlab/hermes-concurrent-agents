# Subagent policy

## Prefer durable Kanban children for long work

Top-level concurrency = many tmux-isolated Hermes processes claimed via Kanban.

## Transient subagents (`delegate_task`)

Use only for short bursts. Budgeted by:

- `delegation.max_concurrent_children` in profile config
- `HCA_MAX_SUBAGENT_CREDITS` env
- `hca.plugin` pre_tool_call gate + leases in HCA state DB

When blocked: create Kanban children or continue sequentially — do not spin forever.

## Nesting

Default: `max_spawn_depth=1`, orchestrator profiles must not implement code (toolsets stripped).

## Isolation

Subagents share the parent process for short work; they are **not** a substitute for process isolation. Long parallel work must spawn new tmux slots via Kanban.
