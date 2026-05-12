# Current state report

`hermes-concurrent-agents` is a small local agent swarm for Hermes Agent.

The idea is simple: keep one local model server running, then let several Hermes workers use it at the same time. One worker can research. Another can write code. Another can review. Another can write docs. They coordinate through Hermes kanban instead of fighting over the same chat window or the same files.

This repo targets unified-memory machines first, especially NVIDIA GB10 / DGX Spark. Apple Silicon and other large-memory systems can use the same pattern, although the performance numbers will differ.

## Why this exists

Most agent work is naturally parallel.

A single agent usually does work in a line: research, plan, code, test, document, review. That is easy to follow, but slow. Human teams do not work that way when the work can be split. A researcher can gather context while an engineer builds. A QA person can review one piece while someone else writes docs.

This project gives Hermes Agent that kind of structure on one local machine.

## Architecture

The usual GB10 setup looks like this:

```text
vLLM / OpenAI-compatible API on :8000
  -> creative-worker
  -> coder-worker
  -> research-worker
  -> qa-worker
  -> orchestrator
       -> delegate_task subagents when needed
```

The model server stays loaded once. Workers are separate Hermes profiles, usually started in tmux sessions. The shared task board is Hermes kanban, backed by SQLite.

That separation matters. Each worker has its own config, memory, session history, and role file. The shared board gives the swarm task ownership, dependency tracking, stale task recovery, and an audit trail.

## Worker roles

- `creative-worker`: reports, docs, summaries, stories, launch posts
- `coder-worker`: implementation, scripts, automation, fixes
- `research-worker`: source gathering, comparisons, papers, technical analysis
- `qa-worker`: tests, review, verification, fact checking
- `orchestrator`: planning, decomposition, routing, acceptance checks

## Current grade

The repo now has a documented 100/100 repository-readiness score.

That score means the project has the expected public-release machinery: docs, setup flow, benchmark artifact generation, CI, smoke tests, safety checks, and a written rubric.

It does not mean every hardware performance claim has been externally reproduced. Real speed claims should cite a benchmark artifact directory from `benchmarks/YYYYMMDDTHHMMSSZ/`.

## Tested results

Known GB10 notes:

- 1 agent: about 23 tok/s
- 3 agents: about 69 tok/s total, about 23 tok/s per agent
- GPU memory: about 85.9GB with 0.70 memory fraction, 64K context, FP8 KV cache

The README includes expected 4-worker and 6-worker rows, but those should stay marked as estimates until a real benchmark run produces artifacts for them.

## Validation status

These checks passed locally during the gap-closure pass:

```bash
bash -n setup.sh scripts/*.sh
bash scripts/validate-docs.sh
bash scripts/benchmark.sh --dry-run --levels 1,2
bash scripts/smoke-kanban-flow.sh
bash scripts/smoke-kanban-flow.sh --dry-run
bash scripts/fault-injection-test.sh
bash scripts/health-monitor.sh --once
```

A code-quality/safety subagent approved the shell and docs changes. A separate spec-review subagent timed out, so it is not counted as evidence.

## What changed in the release-readiness pass

Added:

- `docs/grade/rubric.md`
- `docs/grade/current-score.md`
- `docs/grade/evidence-map.md`
- `docs/benchmarking.md`
- `docs/durability-tests.md`
- `docs/use-cases.md`
- `docs/current-state-report.md`
- `scripts/validate-docs.sh`
- `scripts/smoke-kanban-flow.sh`
- `scripts/fault-injection-test.sh`
- `.github/workflows/ci.yml`
- `CHANGELOG.md`
- `CONTRIBUTING.md`
- `benchmarks/.gitkeep`

Improved:

- safer `setup.sh` with `--dry-run`, `--force`, dependency checks, config preservation, and timestamped backups
- real benchmark artifact generation in `scripts/benchmark.sh`
- readiness checks in `scripts/spawn.sh`
- `--once` and safer NVIDIA memory parsing in `scripts/health-monitor.sh`
- clearer deployment docs: vLLM is canonical on GB10; SGLang is experimental on SM121

## What to do next

The repo is ready for a clean benchmark evidence run.

```bash
bash scripts/benchmark.sh   --levels 1,2,3,4,6   --endpoint http://127.0.0.1:8000/v1   --model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
```

Then update the README performance table so every number is either measured with an artifact path or clearly marked estimated.
