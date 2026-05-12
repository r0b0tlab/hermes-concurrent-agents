# Evidence Map

| Rubric area | Evidence files / commands |
|---|---|
| Vision | `README.md`, `SKILL.md` |
| Architecture | `README.md`, `docs/workflow-patterns.md`, `profiles/*/SOUL.md` |
| Setup | `setup.sh --help`, `setup.sh --dry-run`, `config/profile-template.yaml` |
| Benchmarks | `scripts/benchmark.sh --dry-run`, `benchmarks/<run>/metrics.json`, `benchmarks/<run>/summary.csv` |
| Operations | `scripts/spawn.sh --help`, `scripts/status.sh`, `scripts/shutdown.sh`, `scripts/health-monitor.sh --once` |
| Kanban | `scripts/smoke-kanban-flow.sh --dry-run`, `hermes kanban --help` |
| Durability | `scripts/fault-injection-test.sh --dry-run`, `scripts/shutdown.sh`, `scripts/spawn.sh` |
| Docs | `scripts/validate-docs.sh`, `docs/current-state-report.md`, `docs/use-cases.md` |
| Packaging | `LICENSE`, `CHANGELOG.md`, `CONTRIBUTING.md`, `.github/workflows/ci.yml` |
| Safety | GB10 flags in README/docs, setup backups, health thresholds |

## Validation commands

```bash
bash -n setup.sh scripts/*.sh
bash scripts/validate-docs.sh
bash scripts/benchmark.sh --dry-run --levels 1,2
bash scripts/smoke-kanban-flow.sh --dry-run
bash scripts/fault-injection-test.sh --dry-run
```
