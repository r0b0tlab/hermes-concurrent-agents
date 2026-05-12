# Changelog

## 1.0.1 - 2026-05-12

### Added
- 100-point grading rubric and evidence map in `docs/grade/`.
- Plain-English project status report in `docs/current-state-report.md`.
- Practical workflow examples in `docs/use-cases.md`.
- Reproducible benchmark artifact generation with real OpenAI usage-token capture.
- CI workflow for shell syntax, docs validation, dry-run benchmark, kanban smoke dry-run, and fault-injection dry-run.
- `scripts/validate-docs.sh`, `scripts/smoke-kanban-flow.sh`, and `scripts/fault-injection-test.sh`.
- Safer setup guidance: dry-run, force, and config-backup behavior.

### Changed
- Benchmarking now distinguishes measured token metrics from synthetic CI dry-runs.
- Documentation separates tested GB10 evidence from expected/portable behavior.

## 1.0.0 - 2026-05-12

Initial public release by @mr-r0b0t — r0b0tlab.
