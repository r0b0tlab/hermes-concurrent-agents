# Current state

`hermes-concurrent-agents` is an **alpha, single-host Hermes team orchestration
control plane**. Its product path is one goal → bounded persisted task graph →
concrete isolated Hermes workers → optional review/rework → one evidence-backed
result.

## Implemented and exercised

- Human operations: `hca run`, `run-status`, `respond`, `collect`, and `stop`.
- Equivalent five-tool Hermes plugin surface backed by the same `FleetService`.
- Explicit decomposition barrier and controller-owned persisted graph.
- One-step/one-worker behavior and explicit independence required for fan-out.
- Concrete profile/worktree routing with reservation-before-claim admission.
- Exact PID plus procfs start-tick ownership and bounded process-group cleanup.
- Restart-safe run state, questions, cancellation, review/rework, and result
  manifests.
- Conservative admission when device or endpoint telemetry is unavailable.
- Stable Hermes private-API compatibility lane for `0.18.2 / 2026.7.7.2`.
- Generic deterministic endpoint tests and on-device single-GB10 orchestration
  acceptance.

The generated [support matrix](support-matrix.md) is authoritative for release
claims. CI separates required stable-Hermes contracts from an advisory latest-
main drift probe.

## Explicit limitations

- Remote agent placement is unsupported. Legacy remote-start paths fail before
  SSH or worker/profile side effects. Read-only SSH inventory/doctor helpers are
  experimental and do not prove placement support.
- HCA does not provision or serve models, normalize providers, own credentials,
  or implement endpoint fallback. Those remain Hermes/operator concerns.
- Host-level filesystem sandboxing is not claimed. HCA proves task-scoped
  profiles/worktrees and checks cross-task writes in controlled fixtures.
- macOS is a portable CI target, not an on-device acceptance claim.
- No universal optimal worker count or performance multiplier is claimed.
- Cluster presets have been removed from the stable package surface.

## Release status

No package, tag, GitHub Release, or remote agent-placement feature is implied by
this report. Build artifacts are verification inputs until the complete clean-
install, detached-worktree, anonymous-clone, and public-source gates pass.

For upgrade and rollback behavior, see [Migration](migration.md). For process,
credential, approval, and graph boundaries, see [Security](security.md).
