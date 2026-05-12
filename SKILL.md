---
name: hermes-concurrent-agents
description: "Deploy multiple concurrent Hermes Agent workers on unified-memory GPUs (GB10, DGX Spark, Apple Silicon) for maximum total tok/s. Profile-isolated, kanban-coordinated, crash-recovering multi-agent system."
version: 1.0.0
author: "@mr-r0b0t — r0b0tlab"
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [multi-agent, concurrent, throughput, gb10, dgx-spark, tmux, kanban, profiles, performance]
    homepage: https://github.com/r0b0tlab/hermes-concurrent-agents
    related_skills: [tmux-agent-teams, kanban-orchestrator, kanban-worker, hermes-agent]
---

# Hermes Concurrent Agents

Run multiple Hermes Agent workers concurrently on a single machine with unified memory (NVIDIA GB10/DGX Spark, Apple Silicon M-series) to maximize total tokens-per-second across parallelizable tasks.

**Key insight:** On unified-memory hardware, a sparse MoE model (like Nemotron 3 Nano 30B-A3B) running with continuous batching can serve 3-6 concurrent agents at ~3x the throughput of a single agent. Tested on GB10: 3 agents = 69 tok/s total (23 each) vs 23 tok/s solo.

## When to Use This Skill

- You have a unified-memory GPU (GB10, DGX Spark, Apple Silicon) and want to maximize throughput
- You need to run multiple independent tasks in parallel (research, coding, writing, analysis)
- You want long-running agents that survive crashes and resume automatically
- You want profile-isolated workers with specialized personas and skills

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Unified Memory GPU (128GB)                              │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Inference Backend (vLLM/SGLang/Ollama)         │    │
│  │  Continuous batching, PagedAttention, MPS        │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         │ OpenAI-compatible API          │
│  ┌──────────────────────┼──────────────────────────┐    │
│  │         kanban.db (shared task board)            │    │
│  └───┬──────────────┬───────────────┬──────────────┘    │
│      │              │               │                    │
│  tmux:worker-1   tmux:worker-2   tmux:worker-3          │
│  hermes -p       hermes -p       hermes -p              │
│  creative        coder           researcher             │
│  (isolated)      (isolated)      (isolated)             │
│                                                         │
│  Orchestrator: main hermes session or gateway            │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Clone the project
git clone https://github.com/r0b0tlab/hermes-concurrent-agents.git
cd hermes-concurrent-agents

# Run setup (creates profiles, configures inference backend reference)
bash setup.sh

# 4. Start inference backend (vLLM + Marlin for GB10):
export VLLM_NVFP4_GEMM_BACKEND=marlin
export VLLM_USE_FLASHINFER_MOE_FP4=0

# 5. Spawn workers:
bash scripts/spawn.sh 3        # spawn 3 workers
bash scripts/benchmark.sh      # benchmark concurrency 1-6
bash scripts/health-monitor.sh # watch GPU/memory
```

## Prerequisites

1. **Hermes Agent installed** — `curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash`
2. **Inference backend running** — vLLM (recommended), SGLang (experimental), or Ollama on localhost
3. **tmux installed** — `apt install tmux` or `brew install tmux`

## Worker Profiles

The setup script creates these isolated profiles:

| Profile | Role | SOUL.md Focus | Best For |
|---------|------|---------------|----------|
| `creative-worker` | Long-form generation | Outline first, save frequently, never truncate | Stories, scripts, reports, worldbuilding |
| `coder-worker` | Implementation | Plan first, commit often, test after changes | Code, APIs, scripts, automation |
| `research-worker` | Analysis & research | Cite sources, save raw data separately | Papers, comparisons, data analysis |
| `qa-worker` | Testing & review | Find bugs, verify claims, report specifics | Code review, testing, fact-checking |
| `orchestrator` | Task routing | Decompose, don't execute. Route to specialists. | Kanban coordination, task planning |

Each profile has:
- Isolated config (can point to different models)
- Isolated memory (SQLite state.db)
- Isolated sessions
- Specialized SOUL.md persona
- Curated skills (irrelevant skills removed)

## Coordination: Kanban Board

Workers coordinate through a shared kanban board (SQLite), not raw tmux send-keys:

```bash
# Create tasks
hermes kanban create "Write chapter 1" --assignee creative-worker
hermes kanban create "Build API" --assignee coder-worker
hermes kanban create "Research topic X" --assignee research-worker

# Link dependencies
hermes kanban link <research-task-id> <synthesis-task-id>

# Workers auto-claim via gateway dispatcher
hermes gateway start
```

Benefits over tmux send-keys:
- Tasks survive worker crashes (auto-reclaimed by dispatcher)
- Dependency linking (child waits for parent to complete)
- Atomic claiming (no duplicate work)
- Audit trail in SQLite forever
- Human-in-the-loop via `kanban_block`

## Workflow Patterns

### Pattern A: Parallel Independent Tasks
Best for: batch research, data processing, parallel file analysis
```bash
# N workers each claim and execute independent tasks
hermes kanban create "Analyze dataset A" --assignee research-worker
hermes kanban create "Analyze dataset B" --assignee research-worker
hermes kanban create "Analyze dataset C" --assignee research-worker
```

### Pattern B: Pipeline with Dependencies
Best for: software development, document drafting with review
```bash
# Plan -> Implement -> Test -> Ship
hermes kanban create "Plan feature" --assignee orchestrator
hermes kanban create "Implement feature" --assignee coder-worker --parent <plan-id>
hermes kanban create "Test feature" --assignee qa-worker --parent <impl-id>
```

### Pattern C: Fan-Out / Fan-In
Best for: long-form writing, multi-part content
```bash
# Outline -> parallel chapters -> edit pass
hermes kanban create "Write outline" --assignee creative-worker
hermes kanban create "Write ch1" --assignee creative-worker --parent <outline-id>
hermes kanban create "Write ch2" --assignee creative-worker --parent <outline-id>
hermes kanban create "Edit and unify" --assignee creative-worker --parent <ch1-id> --parent <ch2-id>
```

### Pattern D: Competitive (GLADIATOR)
Best for: creative exploration, A/B testing
```bash
# Same task, multiple workers, pick best result
hermes kanban create "Design landing page v1" --assignee creative-worker
hermes kanban create "Design landing page v2" --assignee creative-worker
hermes kanban create "Design landing page v3" --assignee creative-worker
```

## Long-Running Task Support

### Session Continuity
Workers use `--continue` to resume after crashes:
```bash
tmux new-session -d -s creative 'hermes -p creative-worker --continue'
```

### Checkpoints
Enable filesystem snapshots before every write:
```yaml
# In profile config.yaml
checkpoints:
  enabled: true
  max_snapshots: 20
```

### Context Explosion Prevention
Each worker's SOUL.md includes rules to prevent context bloat:
- Save intermediate results to disk, not conversation
- Summarize completed subtasks before moving on
- Use read_file tool instead of re-sending file contents

## Performance Tuning

### Optimal Concurrency (not max utilization)
The goal is **peak total tok/s**, not 100% GPU utilization:

| Concurrency | Expected Total TPS | Per-Agent TPS | Notes |
|-------------|-------------------|---------------|-------|
| 1 | ~23 tok/s | 23 | Baseline (tested on GB10) |
| 2 | ~46 | ~23 | ~2x total |
| 3 | ~69 | ~23 | ~3x total (tested, sweet spot) |
| 4 | ~80-95 | 20-24 | ~3.5x total (estimated) |
| 6 | ~90-110 | 15-18 | ~4x total, diminishing returns |

### Memory Budget (128GB unified)
```
Model weights:     ~25-40 GB (NVFP4)
KV cache:          ~40-60 GB (shared across agents)
OS + agents:       ~15-20 GB
Buffer:            ~10-15 GB  ← don't skip this
```

**Key Flags (vLLM + Marlin)**
- `VLLM_NVFP4_GEMM_BACKEND=marlin` — CRITICAL: CUTLASS FP4 broken on SM121
- `VLLM_USE_FLASHINFER_MOE_FP4=0` — disable broken FlashInfer FP4 path
- `--gpu-memory-utilization 0.70` — leave 30% for OS and agents
- `--max-model-len 65536` — 64K context (Hermes minimum)
- `--max-num-seqs 16` — concurrent request limit
- `--kv-cache-dtype fp8` — halve KV cache memory
- `--moe-backend marlin` — force Marlin MoE backend

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/spawn.sh N` | Spawn N worker tmux sessions |
| `scripts/shutdown.sh` | Graceful shutdown of all workers |
| `scripts/benchmark.sh` | Benchmark concurrency 1-6, report tok/s |
| `scripts/health-monitor.sh` | Watch GPU/memory/disk, alert on thresholds |
| `scripts/status.sh` | Show all workers, kanban board, GPU status |

## Configuration

Workers can use different models for different tasks:
```yaml
# research-worker config (fast model for search/summarization)
model:
  default: qwen3-8b
  provider: custom:local-ollama
  base_url: http://127.0.0.1:11434/v1

# creative-worker config (high-quality for generation)
model:
  default: nemotron-30b-nvfp4
  provider: custom:local-vllm
  base_url: http://127.0.0.1:30000/v1
```

## Pitfalls

1. **Running at 100% GPU memory** — always leave 20-30% headroom for OS, agents, and KV cache spikes
2. **Not using MPS** — without MPS, CUDA context switching kills throughput
3. **SGLang sgl_kernel on SM121** — no prebuilt sm121 binaries. Use vLLM.
4. **CUTLASS FP4 on SM121** — lacks tcgen05 tensor cores. Set VLLM_NVFP4_GEMM_BACKEND=marlin.
5. **No GPU memory cap** — always set --gpu-memory-utilization 0.70 or system freezes.
4. **Shared file writes** — two agents writing the same file = corruption. Use kanban to coordinate.
5. **Context explosion** — agents re-sending history bloats KV cache. Enforce SOUL.md rules.
6. **No crash recovery** — always use `--continue` and enable checkpoints.
7. **One-size-fits-all model** — use fast models for research, big models for generation.
8. **Skipping the benchmark** — your hardware's sweet spot may differ. Always measure.

## Compatibility

- **Hardware:** NVIDIA GB10, DGX Spark, Apple Silicon M-series, any unified-memory GPU
- **OS:** Linux (primary), macOS (secondary)
- **Hermes Agent:** v2.0+ (profiles, kanban, delegation)
- **Inference backends:** vLLM (recommended), SGLang (experimental on SM121), Ollama, llama.cpp
- **Models:** Any OpenAI-compatible API. Optimized for MoE sparse models (30B-A3B, 8B active).

## Links

- [Research Report](references/research-report-summary.md) — full analysis of GB10 concurrent throughput
- [Deployment Guide](docs/deployment-guide.md) — step-by-step setup
- [Tuning Guide](docs/tuning-guide.md) — performance optimization
- [Workflow Patterns](docs/workflow-patterns.md) — detailed pattern examples
