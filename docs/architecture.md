# Architecture

## Product flow

```text
one user goal
  → small bounded team + validated dependency graph
  → Hermes Kanban task/run truth
  → concrete profile/worktree worker slots
  → admission + exact ownership + supervision
  → conditional review/rework or scoped human input
  → one evidence-backed result + cleanup state
```

HCA is deliberately narrower than a general agent framework. It reuses Hermes
profiles, sessions, Kanban, workspaces, artifacts, plugins, and approvals.

## Planes and ownership

| Plane | Owner | Responsibility |
|---|---|---|
| Goal/result service | HCA `FleetService` | Versioned run operations, idempotency, result schemas |
| Task truth | Hermes Kanban | Tasks, dependencies, claims, run rows, comments, completion |
| Graph boundary | HCA controller | Validated child IDs, review/rework/final barriers, expansion denial |
| Admission/routing | HCA | Capacity, concrete free slot reservation before claim |
| Worker execution | Hermes + tmux | Task-scoped session in a concrete profile/workspace |
| Process ownership | HCA | PID + start ticks + process group, restart-safe reconciliation |
| Model/provider/tools | Hermes/operator | Credentials, endpoint, model, fallback, ordinary tools |
| Optional telemetry | HCA adapters | Conservative admission signals; never serving ownership |

HCA state is a mapping/projection and ownership journal, not a second editable
Kanban database.

## Concurrency

One-step work uses one worker. Multiple acceptance criteria do not automatically
fan out: the caller must declare them independent. The admitted wave is bounded
by validated ready DAG width, requested concurrency, concrete free slots, role
caps, active leases, configured sequence credits, memory hysteresis, disk
headroom, and optional endpoint telemetry.

Fleet high-level-run slots, worker attempts, sequence credits, task retries,
supervisor replacements, and absolute deadlines are separate authorities. A
recovery tick dispatches only remaining capacity and cannot expand the persisted
task graph.

Unknown telemetry is conservative unknown, never unlimited capacity. No
universal worker count is encoded as a product claim.

## Isolation and ownership

- **Profile/session:** one concrete Hermes profile/session per active task.
- **Workspace:** canonical task worktree for writers; explicit policy for
  read-only/no-workspace roles.
- **Process:** one exact PID/start-tick/process-group identity per worker run.
- **Logs:** board/task/upstream-run names prevent board-local run-ID collisions.
- **Graph:** only persisted HCA child IDs are dispatchable; unauthorized tasks
  are quarantined and audited.

These controls prevent accidental cross-task contamination in tested workflows.
They do not claim a host sandbox against malicious same-user code.

## Completion and recovery

A task row becoming terminal is insufficient while its exact worker remains
live. Final state is derived from upstream terminal evidence, result/artifact
handoff, required review, open questions, and exact worker liveness. Restart
reconciliation releases stale claims/leases only after ownership checks and
never signals a reused PID.

Operator cancellation, deadline timeout, worker crash, supervisor replacement,
and identity-isolation failure are recorded as distinct termination classes.
Exact recovery preserves the canonical worktree and can reassign only to a
configured concrete profile; it does not restart endpoints or mutate run
concurrency.

## Remote boundary

Stable HCA is single-host for workers and Kanban task truth. A model endpoint may
be remote through Hermes. Remote agent placement is unsupported because no safe
remote Kanban claim/heartbeat transport is available. HCA does not introduce a
distributed task database, NFS SQLite, or ad hoc replication.

## Non-goals

- Provisioning, serving, or qualifying models
- Provider normalization or endpoint fallback
- Replacing Hermes Kanban, profiles, sessions, artifacts, or approvals
- Universal capability brokering
- Host-level sandboxing
- Stable remote agent placement
- Universal performance/concurrency claims
