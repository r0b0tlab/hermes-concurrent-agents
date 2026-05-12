# Concurrency Swarm 100-Point Rubric

This rubric grades `hermes-concurrent-agents` as a public, reproducible concurrency swarm project.

| Category | Points | Full-credit standard |
|---|---:|---|
| 1. Vision and problem definition | 8 | Clear problem, target users, hardware assumptions, success criteria, and why concurrency matters. |
| 2. Architecture and coordination design | 12 | Clear swarm architecture, isolation boundaries, task lifecycle, orchestrator/worker roles, and recovery model. |
| 3. Reproducible setup and onboarding | 12 | Fresh clone to safe setup, dependency checks, idempotent scripts, non-destructive profile config handling, backend verification. |
| 4. Benchmark rigor and performance evidence | 15 | Real token metrics, concurrency sweep, backend/environment manifest, logs, artifacts, repeatable method. |
| 5. Worker lifecycle and operations | 10 | Spawn/status/shutdown/health scripts, readiness detection, prefix-scoped cleanup, log capture, restart guidance. |
| 6. Kanban integration and task correctness | 10 | Atomic claim, dependencies, stale reclaim, no duplicate completion, human-block path, runnable smoke proof. |
| 7. Durability and fault tolerance | 10 | Crash/restart/backend-down/resource-pressure/file-conflict scenarios tested or clearly simulated. |
| 8. Documentation quality | 10 | Accurate README/SKILL/docs, tested vs expected claims separated, examples match scripts, local links valid. |
| 9. Packaging, release, and maintainability | 7 | License, changelog, contribution guide, CI/static checks, release checklist, repo hygiene. |
| 10. Safety and resource management | 6 | GPU memory caps, disk/memory checks, non-destructive defaults, no secrets, freeze-avoidance guidance. |
| **Total** | **100** | |

## Grade bands

- 95-100: Reference-grade public release. Reproducible evidence, robust automation, explicit limitations.
- 90-94: Strong release. Minor portability or hardware-artifact limitations remain.
- 80-89: Solid beta. Useful and credible, but still has manual/evidence gaps.
- 70-79: Strong prototype. Concept and scripts exist; reproducibility incomplete.
- 60-69: Concept demo. Limited verification.
- <60: Exploratory notes, not reliably runnable by others.
