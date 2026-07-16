# Running a team (goal → result)

The canonical HCA path is two commands:

```bash
hca run "Build/research/ship this goal"
hca status
```

`hca run` turns one desired outcome into a supervised concurrent Hermes team
run and returns one evidence-backed result. Models, endpoints, and tools stay
in your Hermes profile configuration — HCA owns only the team layer.

## The run lifecycle

A run is a finite state machine (`hca run-status <id>` / `hca collect <id>`):

```
queued → planning → running → review → completed
                  ↘ needs_input ↗   ↘ rework ↗
   (any) → stopping → cancelled
   (any) → blocked | failed
```

Only `completed`, `blocked`, `failed`, and `cancelled` are terminal, and only
`completed` means success — it requires accepted verification. A stop is never
turned into a completion.

## Commands

| Command | What it does |
|---|---|
| `hca run "<goal>"` | Start a durable mission; prints the run id and streams progress on a TTY. `--detach` returns immediately. |
| `hca run-status [<id>]` | Run state, or a list of recent runs when the id is omitted. |
| `hca respond <id> <question-id> "answer"` | Answer a `needs_input` question; resumes only the blocked branch. |
| `hca collect <id>` | Deterministic result manifest: outcome, evidence, artifacts, blockers, cleanup, SHA-256. |
| `hca stop <id>` | Cancel a run; preserves partial work; marks `stopping → cancelled`. |

Every command supports `--json`, returns a stable semantic exit code (`0` ok,
`2` invalid, `3` preflight, `4` blocked/needs-input, `5` runtime), and a
`remediation` field.

## Idempotency

A new `hca run` invocation always creates a new run id unless you pass
`--idempotency-key <key>` (identical key ⇒ same run) or `--resume <run-id>`.
Goal text alone never deduplicates. Agent callers must provide or record a
stable idempotency key so retries are safe.

## Human and agent parity

The Hermes plugin exposes the same operations as five tools —
`hca_team_run`, `hca_team_status`, `hca_team_collect`, `hca_team_respond`,
and the approval-gated `hca_team_stop`. Both surfaces call the same typed
service, so a human and a Hermes agent drive identical state transitions and
receive the same result schema. See
[the hca-operations skill](../src/hca/skills/hca-operations/SKILL.md).

## Backend note

Absent an admitted execution backend (a configured Hermes endpoint plus a
running supervisor), `hca run` completes preflight and leaves the run
`blocked` with a precise remediation rather than reporting fabricated success.
Start the supervisor with `hca up` after configuring a Hermes profile/endpoint.
