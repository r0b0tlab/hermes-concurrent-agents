# hermes-concurrent-agents

> **By [@mr-r0b0t on X](https://x.com/mr_r0b0t) — [r0b0tlab](https://github.com/r0b0tlab)**

Run multiple [Hermes Agent](https://github.com/NousResearch/hermes-agent) workers concurrently on a single machine with unified memory (NVIDIA GB10/DGX Spark, Apple Silicon M-series) to maximize total tokens-per-second across parallelizable tasks.

**Why this works:** On unified-memory hardware, a sparse Mixture-of-Experts model (like Nemotron 3 Nano 30B-A3B) with continuous batching serves 4-6 concurrent agents at **2.5-3x** the throughput of a single agent. The GPU processes batches more efficiently than individual requests.

```
┌─────────────────────────────────────────────────────────┐
│  Unified Memory GPU (128GB)                              │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Inference Backend (SGLang / vLLM / Ollama)     │    │
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

# 4. Start your inference backend (example: SGLang)
docker compose -f config/sglang/docker-compose.yml up -d

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
| **Inference backend** | SGLang, vLLM, or Ollama serving a model |
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

## Performance: Optimal vs Maximum

The goal is **peak total tok/s per dollar**, not 100% GPU utilization.

| Concurrency | Total TPS | Per-Agent | Notes |
|-------------|-----------|-----------|-------|
| 1 | ~35 | 35 | Baseline |
| 2 | ~55-60 | 28-30 | 1.6x total |
| 4 | ~80-95 | 20-24 | **2.5x total (sweet spot)** |
| 6 | ~90-110 | 15-18 | 3x total, diminishing returns |
| 8 | ~85-100 | 11-13 | KV cache pressure |

**Memory budget (128GB unified):**
```
Model weights (NVFP4):  25-40 GB
KV cache (shared):      40-60 GB
OS + agents:            15-20 GB
Buffer (don't skip):    10-15 GB
```

**Key flags:**
- `--mem-fraction-static 0.70` — leave 30% headroom, not 15%
- `--max-model-len 32768` — reasonable context, not 256k
- Enable MPS daemon for GPU sharing

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
  default: nemotron-30b-nvfp4
  provider: custom:local-vllm
  base_url: http://127.0.0.1:30000/v1
```

## Pitfalls

| Pitfall | Fix |
|---------|-----|
| Running at 100% GPU memory | Leave 20-30% headroom |
| No MPS daemon | Enable for concurrent CUDA sharing |
| SM120 kernels on SM121 | Use SM121-compiled containers (78% perf loss otherwise) |
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

- [Deployment Guide](docs/deployment-guide.md) — step-by-step setup
- [Tuning Guide](docs/tuning-guide.md) — performance optimization
- [Workflow Patterns](docs/workflow-patterns.md) — detailed examples
- [Research Summary](references/research-report-summary.md) — GB10 throughput analysis

## License

MIT — [r0b0tlab](https://github.com/r0b0tlab) 2026
