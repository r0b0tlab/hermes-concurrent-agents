# Workflow patterns

The primary operation is always one `hca run` goal. HCA chooses a bounded graph;
users should not manually pre-spawn a fleet for ordinary work.

## One-step task

```bash
hca run \
  --source-profile default \
  --project ~/src/app \
  --acceptance "Focused tests pass" \
  "Fix the parsing defect and verify it"
```

One-step work stays at one worker even when the fleet has spare slots.

## Explicitly independent fan-out and fan-in

```bash
hca run \
  --source-profile default \
  --project ~/src/app \
  --acceptance "Audit runtime compatibility" \
  --acceptance "Audit package and CI contracts" \
  --independent-criteria \
  --concurrency 2 \
  --review auto \
  "Produce one integrated release-readiness result"
```

Independent work receives distinct concrete profiles/workspaces. Integration
waits for both branches; final collection waits for integration and any required
review/rework gate.

## Human checkpoint

```bash
hca run-status <run-id>
hca respond <run-id> <question-id> "Use option B"
hca collect <run-id>
```

A blocked worker never waits on an invisible interactive terminal prompt. HCA
persists a scoped question and resumes only the affected branch.

## Cancellation and recovery

```bash
hca stop <run-id>
hca run-status <run-id>
```

HCA persists `stopping` before signaling exact owned process groups. Partial
results and dirty workspaces remain visible. Restarting the controller reconciles
state against exact PID/start-tick and upstream Kanban evidence.

## Remote inference

A selected Hermes profile may point to a model endpoint on another host. Keep
HCA workers and the authoritative Kanban board together. Remote agent fan-out is
unsupported; see [cluster scope](gb10-cluster.md).

## Subagents versus durable children

| Need | Mechanism |
|---|---|
| One bounded assigned task | One concrete HCA worker |
| Durable independent work | Controller-created Kanban child |
| Human decision | Persisted HCA question |
| Optional short subagent burst | Explicit opt-in lease budget only |

Worker delegation is disabled by default. Workers never create unrelated tasks
or expand the persisted HCA graph.
