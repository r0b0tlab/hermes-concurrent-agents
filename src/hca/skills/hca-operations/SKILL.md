---
name: hca-operations
description: >-
  Use HCA to turn one desired outcome into a supervised, isolated, reviewed
  concurrent Hermes team run. Use when you need durable parallel work tracked
  in Kanban, capacity-aware admission, crash recovery, and a single
  evidence-backed result — not a one-off tool call.
---

# HCA operations

HCA (Hermes Concurrent Agents) turns **one goal** into a supervised concurrent
team of isolated Hermes workers and returns **one evidence-backed result**. It
does not provision models, route providers, or duplicate Hermes Kanban — it
owns team composition, safe concurrency, isolation, review, recovery, and
result collection on top of your existing Hermes profiles.

## When to use it

- The work decomposes into independent tasks that benefit from parallelism.
- You want durable, Kanban-tracked execution that survives crashes.
- You need an independent reviewer before code/publish/destructive changes.

Do **not** reach for it for a single quick answer — a normal tool call is
cheaper. Concurrency is an optimization, not a quota to fill.

## The five team tools

1. `hca_team_run(goal, project?, team?, concurrency?, idempotency_key?)` —
   submit a durable mission. Returns a `run_id` handle. **Always pass a stable
   `idempotency_key`** so a retry returns the same run instead of a duplicate.
2. `hca_team_status(run_id?)` — what the team is doing, which agents are
   active, what is blocked, whether input is required, where the outputs are.
   Omit `run_id` to list recent runs.
3. `hca_team_collect(run_id)` — the deterministic result manifest: outcome,
   evidence, artifacts, unresolved blockers, cleanup. It **never** reports
   cancelled or blocked work as success.
4. `hca_team_respond(run_id, question_id, response)` — answer a structured
   `needs_input` question. Only the matching blocked branch resumes.
5. `hca_team_stop(run_id)` — cancel a run (approval-gated). Marks
   `stopping → cancelled`, preserves partial work, and never becomes a
   completion.

## Reading results

Every result carries a semantic `code`: `0` ok, `2` invalid input, `3`
preflight/capability failure, `4` blocked/needs-input, `5` runtime failure —
plus a `remediation` string telling you what to do next. Branch on `state`
and `code`, not prose.

`state` is finite: `queued`, `planning`, `running`, `needs_input`, `review`,
`rework`, `stopping`, `completed`, `blocked`, `failed`, `cancelled`. Only the
last four are terminal, and only `completed` means success (it requires
accepted verification).

## Avoiding duplicate dispatchers

An HCA-owned board must have exactly one dispatcher. If a Hermes gateway is
running with `kanban.dispatch_in_gateway: true` on that board, HCA fails
closed rather than racing it. Resolution: set `dispatch_in_gateway: false` in
the participating profile(s) and restart the gateway, or stop the gateway.
Check with `hca doctor --json` (`compat.dispatcher_ownership`).

## Inspecting capacity

`hca doctor --json` reports the selected device adapter, the compatibility
lane, and admission signals. HCA admits only safe, useful concurrency for the
current device/endpoint; unknown telemetry is treated conservatively, never as
infinite capacity.
