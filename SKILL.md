---
name: hermes-concurrent-agents
description: Supervise bounded Hermes teams on one host.
version: 2.0.0
---

# hermes-concurrent-agents

Use HCA when one goal should become a small, persisted, supervised Hermes team
with exact ownership and one evidence-backed result.

## Preconditions

- Hermes Agent is installed, authenticated, and configured.
- Stable contract lane: Hermes `0.18.2 / 2026.7.7.2`.
- `tmux` is available.
- Workers and the authoritative Kanban board remain on one host.
- Any model endpoint, local or remote, is already configured through Hermes.

## Primary workflow

```bash
hca init --preset generic-linux --model <id> --source-profile default
hca doctor
hca run \
  --source-profile default \
  --acceptance "The requested result is verified" \
  "Complete the goal"
hca run-status <run-id>
hca collect <run-id>
```

One-step work uses one worker. Fan-out requires multiple criteria plus
`--independent-criteria`; set a bounded `--concurrency` only when work is truly
independent.

## Human interaction

```bash
hca respond <run-id> <question-id> "answer"
hca stop <run-id>
```

Never answer a different run's question or treat blocked/cancelled work as
success. Stop is confirmation/approval-gated and signals only exact HCA-owned
process groups.

## Principles

1. Hermes Kanban is task/run truth; HCA owns only its bounded graph and mappings.
2. Reserve capacity and a concrete slot before claim.
3. Workers cannot create or dispatch unrelated graph work.
4. Durable fan-out uses controller-created Kanban children; worker delegation is
   disabled by default.
5. Unknown telemetry is conservative, never unlimited.
6. Completion requires evidence, barriers, review state, and worker cleanup.
7. HCA does not provision models, copy credentials, normalize providers, or own
   fallback.
8. Remote model endpoints are supported; remote agent placement is unsupported.

## Verification

Before release or a substantial upgrade:

```bash
scripts/release-check.sh --full
```

Read `docs/running-a-team.md`, `docs/support-matrix.md`, `docs/security.md`, and
`docs/migration.md` for the complete contracts.
