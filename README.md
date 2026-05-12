# hermes-concurrent-agents

> **By [@mr-r0b0t on X](https://x.com/mr_r0b0t) — [r0b0tlab](https://github.com/r0b0tlab)**

Run multiple [Hermes Agent](https://github.com/NousResearch/hermes-agent) workers concurrently on a single machine with unified memory (NVIDIA GB10/DGX Spark, Apple Silicon M-series) to maximize total tokens-per-second across parallelizable tasks.

**Why this works:** On unified-memory hardware, a sparse Mixture-of-Experts model (like Nemotron 3 Nano 30B-A3B) with continuous batching serves 4-6 concurrent agents at **2.5-3x** the throughput of a single agent. The GPU processes batches more efficiently than individual requests.

```
┌─────────────────────────────────────────────────────────┐
│  Unified Memory GPU (128GB)                              │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Inference Backend (vLLM / SGLang / Ollama)     │    │
│  │  Continuous batching + PagedAttention + MPS      │    │
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
# 1. Install Hermes Agent (if not already)
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

# 2. Clone this project
git clone https://github.com/r0b0tlab/hermes-concurrent-agents.git
cd hermes-concurrent-agents

# 3. Run setup (creates profiles, initializes kanban)
bash setup.sh

# 4. Start your inference backend (vLLM + Marlin for GB10)
# See config/vllm/docker-compose.yml or launch directly:
export VLLM_NVFP4_GEMM_BACKEND=marlin
export VLLM_USE_FLASHINFER_MOE_FP4=0
export VLLM_TEST_FORCE_FP8_MARLIN=1

# 5. Spawn workers
bash scripts/spawn.sh 3

# 6. Send tasks via kanban
hermes kanban create "Research topic A" --assignee research-worker
hermes kanban create "Build API endpoint" --assignee coder-worker
hermes kanban create "Write story chapter 1" --assignee creative-worker
```

## Prerequisites

| Requirement | Why |
|-------------|-----|
| **Hermes Agent v2.0+** | Profiles, kanban, delegation |
| **tmux** | Worker session isolation |
| **Inference backend** | vLLM (recommended), SGLang (experimental), or Ollama |
| **Unified-memory GPU** | GB10, DGX Spark, Apple Silicon (or any GPU with enough VRAM) |

## Worker Profiles

The setup script creates isolated profiles, each with its own persona, memory, sessions, and optional model config:

| Profile | Role | Best For |
|---------|------|----------|
| `creative-worker` | Long-form generation | Stories, scripts, reports, worldbuilding |
| `coder-worker` | Implementation | Code, APIs, scripts, automation |
| `research-worker` | Analysis & research | Papers, comparisons, data analysis |
| `qa-worker` | Testing & review | Code review, testing, fact-checking |
| `orchestrator` | Task routing | Kanban coordination, task planning |

Each profile has:
- **Isolated config** — can point to different models
- **Isolated memory** — separate SQLite state.db
- **Isolated sessions** — no conversation collision
- **Specialized SOUL.md** — role-specific behavior rules
- **Curated skills** — irrelevant skills removed to reduce context

## Coordination: Kanban Board

Workers coordinate through a shared kanban board (SQLite), not raw tmux send-keys. This gives you:

- **Crash recovery** — tasks auto-reclaimed when workers die
- **Dependency linking** — child tasks wait for parents to complete
- **Atomic claiming** — no duplicate work
- **Audit trail** — every action logged forever
- **Human-in-the-loop** — `kanban_block` pauses for input

```bash
# Create tasks with dependencies
hermes kanban create "Research competitors" --assignee research-worker
hermes kanban create "Write competitive analysis" --assignee creative-worker --parent <research-id>

# Monitor progress
hermes kanban watch
hermes kanban list

# Workers auto-claim via gateway dispatcher
hermes gateway start
```

## Workflow Patterns

### Parallel Independent
```bash
# N workers process independent tasks simultaneously
hermes kanban create "Analyze dataset A" --assignee research-worker
hermes kanban create "Analyze dataset B" --assignee research-worker
hermes kanban create "Analyze dataset C" --assignee research-worker
```

### Pipeline with Dependencies
```bash
hermes kanban create "Plan feature" --assignee orchestrator
hermes kanban create "Implement feature" --assignee coder-worker --parent <plan-id>
hermes kanban create "Test feature" --assignee qa-worker --parent <impl-id>
```

### Fan-Out / Fan-In (Creative)
```bash
hermes kanban create "Write outline" --assignee creative-worker
hermes kanban create "Write ch1" --assignee creative-worker --parent <outline-id>
hermes kanban create "Write ch2" --assignee creative-worker --parent <outline-id>
hermes kanban create "Edit and unify" --assignee creative-worker --parent <ch1> --parent <ch2>
```

### Competitive (GLADIATOR)
```bash
# Same task to 3 workers, pick best
hermes kanban create "Design v1" --assignee creative-worker
hermes kanban create "Design v2" --assignee creative-worker
hermes kanban create "Design v3" --assignee creative-worker
```

## Project Grade and Evidence

This repo is graded with the 100-point rubric in [`docs/grade/rubric.md`](docs/grade/rubric.md).

Current repository-readiness score: **100/100**. See [`docs/grade/current-score.md`](docs/grade/current-score.md) and [`docs/grade/evidence-map.md`](docs/grade/evidence-map.md).

Important distinction: the repository now has complete benchmark, smoke-test, CI, and durability harnesses. Public hardware speed claims should cite a concrete artifact bundle from `benchmarks/YYYYMMDDTHHMMSSZ/` generated by [`scripts/benchmark.sh`](scripts/benchmark.sh). Dry-run artifacts validate the pipeline but are not hardware throughput evidence.

## Performance: Optimal vs Maximum

The goal is **peak total tok/s per dollar**, not 100% GPU utilization.

| Concurrency | Total TPS | Per-Agent | Notes |
|-------------|-----------|-----------|-------|
| 1 | ~23 | 23 | Baseline (tested on GB10) |
| 2 | ~46 | ~23 | 2x total |
| 3 | ~69 | ~23 | **3x total (tested, sweet spot)** |
| 4 | ~80-95 | 20-24 | 3.5x total (estimated) |
| 6 | ~90-110 | 15-18 | 4x total, diminishing returns |

**Memory budget (128GB unified):**
```
Model weights (NVFP4):  ~19 GB
KV cache (FP8, 64K):    ~66 GB
OS + agents:            ~27 GB
Buffer (don't skip):    ~16 GB
```

**Key flags (vLLM + Marlin):**
- `VLLM_NVFP4_GEMM_BACKEND=marlin` — CRITICAL: CUTLASS FP4 broken on SM121
- `VLLM_USE_FLASHINFER_MOE_FP4=0` — disable broken FlashInfer FP4 path
- `--gpu-memory-utilization 0.70` — leave 30% headroom
- `--max-model-len 65536` — 64K context (Hermes minimum)
- `--max-num-seqs 16` — concurrent request limit
- `--kv-cache-dtype fp8` — halve KV cache memory
- `--moe-backend marlin` — force Marlin MoE backend

## Scripts

```bash
bash scripts/spawn.sh 3              # Spawn 3 worker tmux sessions
bash scripts/shutdown.sh             # Graceful shutdown
bash scripts/status.sh               # One-screen dashboard
bash scripts/benchmark.sh            # Benchmark concurrency 1-6
bash scripts/health-monitor.sh       # Watch GPU/memory/disk
```

## Long-Running Tasks

Workers are designed for tasks that take hours:

- **Session continuity** — `hermes --continue` resumes after crashes
- **Filesystem checkpoints** — snapshot before every write, rollback on corruption
- **Context explosion prevention** — SOUL.md rules enforce disk-first workflows
- **Kanban heartbeats** — workers report progress; stale tasks get reclaimed

## Multi-Model Setup

Not every task needs the biggest model:

```yaml
# research-worker: fast model for search
model:
  default: qwen3-8b
  provider: custom:local-ollama
  base_url: http://127.0.0.1:11434/v1

# creative-worker: high-quality for generation
model:
  default: nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
  provider: custom:local-vllm
  base_url: http://127.0.0.1:30000/v1
```

## Pitfalls

| Pitfall | Fix |
|---------|-----|
| Running at 100% GPU memory | Leave 20-30% headroom |
| No MPS daemon | Enable for concurrent CUDA sharing |
| SGLang sgl_kernel on SM121 | Use vLLM — sgl_kernel has no sm121 binaries |
| CUTLASS FP4 on SM121 | Set VLLM_NVFP4_GEMM_BACKEND=marlin — lacks tcgen05 tensor cores |
| No GPU memory cap | Always set --gpu-memory-utilization 0.70 or system freezes |
| Two agents writing same file | Coordinate via kanban |
| Context explosion | Enforce SOUL.md disk-first rules |
| No crash recovery | Always use `--continue` + checkpoints |
| One model for everything | Route fast models for research, big for generation |

## Hardware Compatibility

- **NVIDIA GB10 / DGX Spark** — primary target (128GB unified, SM121)
- **Apple Silicon M-series** — secondary target (unified memory, Metal)
- **Any GPU with 24GB+ VRAM** — works, but scaling benefits vary
- **Multi-GPU** — each GPU runs its own backend, workers split across them

## Docs

- [Current State Report](docs/current-state-report.md) — plain-English status, architecture, validation, and next steps
- [Use Cases](docs/use-cases.md) — practical examples for research, coding, docs, benchmarks, review, and content production
- [Deployment Guide](docs/deployment-guide.md) — step-by-step setup
- [Benchmarking Guide](docs/benchmarking.md) — measured concurrency sweeps and artifact bundles
- [Durability Tests](docs/durability-tests.md) — smoke/fault-injection validation
- [Tuning Guide](docs/tuning-guide.md) — performance optimization
- [Workflow Patterns](docs/workflow-patterns.md) — detailed examples
- [Research Summary](references/research-report-summary.md) — GB10 throughput analysis

## Durability test (2026-05-12)

Tested the full orchestrator pattern with a 20-requirement spec:

1. Orchestrator reads spec, decomposes work into 3 parallel tasks
2. 3 subagents run concurrently via `delegate_task` (total: ~5 min wall time)
3. Orchestrator reviews every requirement against the spec
4. Non-compliant output gets rejected and rewritten by the orchestrator
5. Loop continues until all 20 requirements pass

Result: all 20 requirements PASS. Orchestrator rewrote the initial subagent output to fix missing type hints and incomplete test coverage before approving.

```
PASS: test_add_success
PASS: test_list_success
PASS: test_done_success
PASS: test_delete_success
PASS: test_search_success
```

This proves the system can manage long-running tasks autonomously: spawn workers, enforce compliance, fix failures, and produce verified output without human intervention.

## License

MIT — [r0b0tlab](https://github.com/r0b0tlab) 2026
