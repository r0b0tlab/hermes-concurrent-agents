# Architecture

## Goal

Run many **isolated Hermes Agent sessions** against one (or per-node) shared **vLLM or SGLang** backend on **DGX Spark / GB10**, so a large task completes under concurrent admission without KV/session collisions.

## Planes

| Plane | Owner | Responsibility |
|---|---|---|
| Task truth | Hermes Kanban | create/claim/complete/block, deps, goals, swarm graphs |
| Control | `hca` supervisor | leader lock, warm slots, admit-before-claim, `dispatch_once(spawn_fn=…)` |
| Execution isolation | tmux + profiles | durable slots `hca-<fleet>-<role>-NN`, separate HERMES_HOME |
| Inference | vLLM or SGLang | continuous batching, OpenAI `/v1` |
| Observability | `hca watch/peek/logs/activity/transcript` | human visibility without attach-by-default |
| Cluster fabric | passwordless SSH | after NVIDIA connect-* playbooks |

## Isolation boundaries

1. **Process**: one Hermes worker process per run (tmux pane).
2. **Session**: separate Hermes session / profile home — no shared in-process KV/session state across workers.
3. **Workspace**: git worktrees for writers; shared-readonly for research/qa.
4. **Shared only**: model weights + continuous batcher on the engine.

## Admission

Credits estimate task weight (class, long context, subagents). Cap top-level runs and total sequence credits. Drain flag blocks new admits. Engine capacity adapters normalize vLLM/SGLang metrics.

## Subagents

Short-lived `delegate_task` bursts only, under global lease budget (`HCA_MAX_SUBAGENT_CREDITS` + plugin). Durable parallel work → Kanban children → tmux slots.

## Cluster

- One control Spark owns Kanban SQLite (never NFS).
- Nodes run the same supervisor locally.
- Control places work and probes via `ssh BatchMode`.
- Default: colocate Hermes with the node’s engine (ports 8000 vLLM / 30000 SGLang).

## Non-goals

- Second web dashboard
- Join-token mesh when SSH already works
- Replacing Hermes Kanban
- Claiming universal concurrency numbers without `hca bench`
