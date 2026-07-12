# Integration Plan: Async Subagents for Local Inference

> **Repo:** https://github.com/r0b0tlab/hermes-concurrent-agents  
> **Target Hardware:** Any unified-memory GPU or multi-GPU setup (NVIDIA GB10, DGX Spark, Apple Silicon, multi-GPU workstations, GPU clusters)  
> **Backend:** Any OpenAI-compatible local inference server (vLLM, SGLang, Ollama, llama.cpp, TGI, etc.)  
> **New Hermes Feature:** `delegate_task(background=true)` — async subagent delegation  
> **Goal:** Add async subagent capability as an additive, model-agnostic layer on top of existing kanban/tmux infrastructure

---

## 1. Current State: Local Inference Architecture

### hermes-concurrent-agents (existing)

| Component | What It Does | Notes |
|-----------|-----------|-------|
| `scripts/spawn.sh` | Spawns N tmux sessions with `hermes -p <profile> chat` | Each worker is a full Hermes process competing for the same local backend |
| `hermes kanban` | Shared SQLite task board with claim/complete/block | Tasks persist across worker crashes; auto-reclaim after staleness |
| Worker profiles | 5 isolated personas (creative, coder, research, qa, orchestrator) | Each profile = separate `state.db`, config, SOUL.md; all point to same local endpoint |
| `scripts/status.sh` | Dashboard: GPU, memory, tmux sessions, kanban | No subagent visibility |
| `scripts/benchmark.sh` | Concurrency 1-6 tok/s measurement | Measures backend batching, not subagent efficiency |

**Key insight:** The current architecture uses **persistent tmux workers** that idle until a kanban task appears. This is good for long-running autonomous work but has overhead: each worker is a full Hermes process with its own model client, context window, and memory DB. On unified-memory systems, this multiplies OS overhead without multiplying inference throughput.

### Hermes `delegate_task(background=true)` (new feature)

| Property | Behavior | Local Inference Implication |
|----------|----------|----------------------------|
| **Async execution** | Returns immediately with `delegation_id`; child runs on daemon thread | No new tmux session = no new Hermes process = less OS memory overhead |
| **Result delivery** | Completion event pushed to `process_registry.completion_queue`; re-enters conversation as new turn | Results surface in the orchestrator's main session; no tmux capture-pane needed |
| **Capacity cap** | `delegation.max_async_children` (default 3); rejects when full | Tune to your backend's `--max-num-seqs` or concurrent request limit |
| **Single-task only** | No batch async in v1 — one background subagent per call | Use multiple calls for fan-out; backend batches them if it supports continuous batching |
| **Interruptible** | `interrupt_all()` on shutdown; child survives parent turn interruption | `/stop` or gateway shutdown cleanly terminates background subagents |
| **Context isolation** | Fresh conversation, no parent history; parent must pass all needed context | Subagent loads model weights fresh from backend; same endpoint, same batching benefit |

**Key insight:** Async subagents are **lighter-weight** than tmux workers because they run as threads within the main Hermes process, not as separate processes. They still hit the same local backend and benefit from continuous batching, but with less OS overhead per concurrent unit of work. This is model-agnostic — works with any backend that exposes `/v1/chat/completions`.

---

## 2. Integration Strategy: "Two-Tier Concurrency"

The design principle is **additive, not replacement**. The existing kanban + tmux worker pool handles long-running autonomous missions that must survive crashes. The new async subagent layer handles **task-bound parallel bursts** within the orchestrator's single session — ideal for continuous batching where short-lived parallel requests maximize throughput.

```
┌─────────────────────────────────────────────────────────────┐
│  Local Inference Setup (any GPU / unified memory / CPU)       │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Inference Backend (vLLM / SGLang / Ollama / etc.)  │    │
│  │  OpenAI-compatible API on configured port             │    │
│  │  Continuous batching (if supported)                 │    │
│  └──────────────────────┬──────────────────────────────┘    │
│                         │ /v1/chat/completions                │
│  ┌──────────────────────┼──────────────────────────────┐    │
│  │         kanban.db (shared SQLite)                    │    │
│  └───┬──────────────┬───────────────┬──────────────┬───┘    │
│      │              │               │               │        │
│  tmux:creative  tmux:coder     tmux:research   tmux:qa     │
│  hermes -p      hermes -p      hermes -p       hermes -p   │
│  creative       coder          research        qa         │
│  -worker        -worker         -worker         -worker     │
│  (persistent)   (persistent)    (persistent)    (persistent)│
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  TIER 2: Async Subagent Burst (new)                        │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐                      │
│  │delegate │ │delegate │ │delegate │  ← daemon threads   │
│  │_task(bg)│ │_task(bg)│ │_task(bg)│    in orchestrator  │
│  │  #1     │ │  #2     │ │  #3     │    process          │
│  │research │ │research │ │research │                      │
│  └────┬────┘ └────┬────┘ └────┬────┘                      │
│       └─────────────┴─────────────┘                        │
│         results → completion_queue → re-injected as turn   │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  Orchestrator (main hermes session, profile: orchestrator) │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 1. Reads kanban board                               │   │
│  │ 2. Dispatches async subagents for parallel bursts   │   │
│  │ 3. Routes persistent work to tmux worker pool         │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Model-agnostic advantage:** Tier 2 async subagents do not spawn new tmux sessions or Hermes processes. They reuse the orchestrator's existing backend client connection, reducing per-agent overhead while still achieving N-way concurrency through the backend's continuous batching (if supported). Works with vLLM, SGLang, Ollama, llama.cpp, TGI, or any `/v1/chat/completions` endpoint.

---

## 3. Concrete Changes to the Repo

### 3.1 New Script: `scripts/async-dispatch.sh`

A model-agnostic helper that dispatches async subagents to any local backend.

```bash
#!/usr/bin/env bash
# scripts/async-dispatch.sh
# Dispatch async subagents for parallel task bursts on any local backend
# Usage: async-dispatch.sh --goal "Research X" --toolsets web --count 3
#
# Tuning: defaults to 3 concurrent (configurable via delegation.max_async_children)
# No tmux overhead — subagents run as daemon threads in the orchestrator process
# All subagents hit the backend endpoint configured in the active profile's config.yaml
```

**Why:** The existing `spawn.sh` creates persistent tmux workers (good for long-running tasks). This new script creates **transient, task-bound** background subagents that auto-report results — ideal for short parallel bursts that maximize backend batching throughput, regardless of model or runtime.

### 3.2 Updated `profiles/orchestrator/SOUL.md` — Async Burst Rules

Add a model-agnostic section:

```markdown
## Async Subagent Burst Pattern

When you claim a kanban task tagged `async-burst`:
1. Decompose the task into 2-3 independent subtasks (respect delegation.max_async_children)
2. Dispatch each via `delegate_task(background=true)` with appropriate toolsets
3. All subagents use the same local backend endpoint — the backend batches requests if it supports continuous batching
4. Continue working on other kanban tasks — do NOT wait
5. When subagent results re-enter the conversation, synthesize them
6. Mark the original kanban task complete with the synthesized summary

Model-agnostic rules:
- Never exceed `delegation.max_async_children` (default 3, configurable in config.yaml)
- If capacity rejected, fall back to sync `delegate_task` or queue in kanban for tmux workers
- Always pass the backend endpoint and model name in context if the subagent needs to reference them
- Subagents share the backend but have isolated context — pass all needed state explicitly

Example:
- Task: "Research 3 competing model architectures for throughput"
- Dispatch 3 async subagents, each researching one architecture
- When results arrive, synthesize comparison table and complete kanban task
```

### 3.3 Updated `scripts/status.sh` — Unified Dashboard

Add an **Async Subagents** section and generic GPU metrics (if available):

```bash
# --- Async Subagents ---
echo ""
echo -e "${BLUE}── Async Subagents ────────────────────────────────${NC}"
hermes execute_code "from tools.async_delegation import list_async_delegations; import json; print(json.dumps(list_async_delegations(), indent=2))" 2>/dev/null | head -20 || echo "  No async subagents active"

# --- GPU / Hardware (best-effort) ---
echo ""
echo -e "${BLUE}── GPU / Hardware ─────────────────────────────────${NC}"
if command -v nvidia-smi &>/dev/null; then
    nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw \
        --format=csv,noheader,nounits 2>/dev/null | while IFS=',' read -r name used total util temp power; do
        pct=$(echo "scale=0; $used * 100 / $total" | bc 2>/dev/null || echo "?")
        echo "  $name: ${used}/${total} MB (${pct}%) | GPU: ${util}% | Temp: ${temp}°C | Power: ${power}W"
    done
elif command -v ioreg &>/dev/null; then
    echo "  Apple Silicon detected (use asitop or powermetrics for details)"
else
    echo "  No GPU monitoring available (install nvidia-smi or asitop)"
fi
```

### 3.4 Updated `config/profile-template.yaml` — Generic Tuning

Add delegation tuning section with model-agnostic defaults:

```yaml
# Delegation tuning (Hermes v2.0+)
# Adjust max_async_children to match your backend's concurrent request capacity
delegation:
  max_async_children: 3        # Background subagent concurrency cap
  max_concurrent_children: 3   # Sync batch concurrency cap
  max_spawn_depth: 2           # Allow orchestrator → worker → leaf
  subagent_auto_approve: false # Safe default; set true only for trusted workloads

# Backend reference (fill in during setup.sh)
model:
  default: __MODEL_NAME__
  provider: __PROVIDER_NAME__
  base_url: __ENDPOINT__
```

### 3.5 Updated `README.md` and `SKILL.md` — Model-Agnostic Two-Tier Concurrency

Document the flexible pattern:

```markdown
## Two-Tier Concurrency

### Tier 1: Persistent Worker Pool (tmux + kanban)
Best for: Long-running autonomous work, overnight jobs, always-on agents, crash recovery
- Spawn with `bash scripts/spawn.sh 3`
- Workers claim kanban tasks and work for hours
- Survive crashes via `--continue` and kanban reclaim
- Each worker is a full Hermes process with isolated memory

### Tier 2: Async Subagent Burst (delegate_task background=true)
Best for: Parallel research bursts, A/B comparisons, multi-source analysis
- Dispatch from orchestrator session with `delegate_task(background=true)`
- Up to N concurrent background subagents (configurable via `delegation.max_async_children`)
- No new tmux session = no new Hermes process = less OS overhead
- All subagents share the same backend connection; backend batches requests if supported
- Results re-enter conversation automatically when complete

### When to Use Which
| Scenario | Tier | Why |
|----------|------|-----|
| "Analyze 4 datasets overnight" | Tier 1 | Long-running, needs crash recovery, persistent worker |
| "Research 3 APIs in parallel" | Tier 2 | Short burst, maximize backend batching, low overhead |
| "Write a 10-chapter report" | Tier 1 | Persistent creative worker with session continuity |
| "Compare 3 design approaches" | Tier 2 | Competitive evaluation, quick turnaround |
| "Continuous PR review" | Tier 1 | Always-on qa-worker via gateway dispatcher |
| "Fan-out: analyze 5 source files" | Tier 2 | 3 async + 2 sync, or queue remainder in kanban |
```

### 3.6 New Benchmark: `scripts/benchmark-async.sh` — Async Overhead Comparison

Measure async subagent efficiency vs persistent tmux workers:

```bash
#!/usr/bin/env bash
# Benchmark async subagent dispatch vs tmux worker spawn
# Metrics: wall time, total tok/s, setup overhead, result latency, GPU/memory delta (if available)
# Produces artifacts in benchmarks/async-YYYYMMDDTHHMMSSZ/
#
# Model-agnostic: works with any backend that exposes /v1/chat/completions
# Captures nvidia-smi if available; otherwise reports OS memory and timing only
```

**Why:** The existing `benchmark.sh` measures backend batching concurrency. This new benchmark measures the **orchestration overhead difference**: async threads vs persistent processes, both hitting the same local backend.

### 3.7 New Workflow Pattern: "Async Burst + Kanban Sync"

Document in `docs/workflow-patterns.md`:

```markdown
## Pattern F: Async Burst with Kanban Synchronization

**When to use:** You need parallel research/analysis NOW, but the results feed into a longer pipeline.

**Flow:**
1. Orchestrator creates kanban task: "Research 3 competing approaches"
2. Orchestrator claims it, dispatches 3 async subagents (one per approach)
3. All 3 subagents hit the local backend simultaneously — backend batches them if it supports continuous batching
4. While subagents run, orchestrator works on other kanban tasks (no blocking)
5. Subagent results re-enter orchestrator conversation with summaries
6. Orchestrator synthesizes comparison, marks original kanban task complete
7. Dependent kanban tasks (e.g., "Write recommendation") auto-unblock

**Memory note:** Async subagents do not add new Hermes processes, so OS memory overhead stays flat. The backend's KV cache or context memory grows with concurrent sequences, but this is the same whether using async or persistent workers. Monitor with `scripts/status.sh`.

**Capacity handling:** If `max_async_children` is reached and another task arrives, the orchestrator either:
- Falls back to sync `delegate_task` (blocks until done)
- Creates a new kanban task for a persistent tmux worker to pick up
```

---

## 4. Implementation Phases

### Phase 1: Foundation (additive, no breaking changes)

| Task | File | Description |
|------|------|-------------|
| 1.1 | `profiles/orchestrator/SOUL.md` | Add async-burst pattern rules (model-agnostic) |
| 1.2 | `scripts/async-dispatch.sh` | Create helper for dispatching N async subagents |
| 1.3 | `scripts/status.sh` | Add async subagent section + generic GPU/hardware detail |
| 1.4 | `config/profile-template.yaml` | Add delegation tuning with generic defaults |
| 1.5 | `README.md` | Document model-agnostic two-tier concurrency |
| 1.6 | `SKILL.md` | Document model-agnostic two-tier concurrency |

### Phase 2: Kanban Integration

| Task | File | Description |
|------|------|-------------|
| 2.1 | `docs/workflow-patterns.md` | Add Pattern F: Async Burst + Kanban Sync |
| 2.2 | `scripts/smoke-kanban-flow.sh` | Add test for async-burst tagged task flow |
| 2.3 | `profiles/orchestrator/SOUL.md` | Refine async-burst rules based on testing |

### Phase 3: Benchmarking & Validation

| Task | File | Description |
|------|------|-------------|
| 3.1 | `scripts/benchmark-async.sh` | Benchmark async vs persistent worker overhead |
| 3.2 | `docs/benchmarking.md` | Document async benchmark methodology |
| 3.3 | `docs/current-state-report.md` | Update with async subagent integration status |

### Phase 4: Advanced Features (optional)

| Task | File | Description |
|------|------|-------------|
| 4.1 | `scripts/kanban-async-bridge.sh` | Auto-dispatch async subagents for tagged tasks |
| 4.2 | `profiles/*/SOUL.md` | Add per-worker async fallback rules |
| 4.3 | `config/` | Document backend-specific `--max-num-seqs` tuning hints |

---

## 5. Multi-Node / Cluster Extension (Future)

For multi-GPU or multi-node setups, the async subagent model extends naturally:

```
┌─────────────────────────────────────────────────────────────┐
│  Multi-Node Cluster (2-N nodes, any GPU type)                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ Node 1      │  │ Node 2      │  │ Node N      │         │
│  │ Backend :8000│  │ Backend :8000│  │ Backend :8000│         │
│  │ N async +   │  │ N async +   │  │ N async +   │         │
│  │ N persistent│  │ N persistent│  │ N persistent│         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│       │                │                │                     │
│       └────────────────┴────────────────┘                     │
│              Shared kanban.db (NFS/SQLite replica)              │
│              Orchestrator dispatches to least-loaded node       │
└─────────────────────────────────────────────────────────────┘
```

**Cluster dispatch rule:** The orchestrator's `async-dispatch.sh` can accept `--node <ip>` to route async subagents to a specific node's backend endpoint. Kanban tasks tagged `async-burst` get decomposed and distributed across nodes for horizontal scaling. Works with any backend on any hardware.

---

## 6. Key Design Decisions (Model-Agnostic)

| Decision | Rationale | Impact |
|----------|-----------|--------|
| **Keep tmux workers** | Async subagents are transient; persistent workers needed for crash recovery and long-running tasks | tmux workers survive orchestrator process restarts; async subagents die with parent |
| **Orchestrator owns async dispatch** | Orchestrator profile already handles task decomposition; adding async rules is lightest integration | Orchestrator runs on the same node as backend; minimal network latency |
| **max_async_children is configurable** | Different backends have different concurrent request limits (vLLM `--max-num-seqs`, Ollama default, etc.) | User tunes to their hardware + backend combo; default 3 is a safe starting point |
| **No new kanban schema** | Use existing `--tag async-burst` rather than adding columns | Keeps kanban SQLite simple; tag is searchable |
| **Single-task async only** | Hermes v1 async does not support batch; document this | Multiple `delegate_task(background=true)` calls still batch in backends that support continuous batching |
| **Status.sh queries async state** | Reuses `list_async_delegations()` API | No new Hermes core changes; works with any backend out of the box |
| **Async subagents share backend connection** | They run in orchestrator process threads | Reduces connection overhead vs N separate tmux workers |

---

## 7. Risk Mitigation (Model-Agnostic)

| Risk | Mitigation | Notes |
|------|------------|-------|
| Async subagent results interrupt current work | By design — results re-enter as new turns; SOUL.md teaches orchestrator to handle gracefully | Backend sequences finish and new turns start; no resource leak |
| Capacity rejection (max_async_children) | Document fallback: retry sync, or queue in kanban for persistent tmux workers | If backend is at its concurrent request limit, sync call waits inline |
| Context isolation means subagents lack kanban state | Pass kanban task ID and relevant context explicitly in `delegate_task(context=...)` | Subagent has no access to orchestrator's SQLite; all state must be in the prompt |
| Nested async (orchestrator spawns orchestrator) | Blocked by `max_spawn_depth=2` default | Prevents runaway sequence growth in backend's context manager |
| Backend crash loses all async subagents | Async subagents die with parent; tmux workers survive | For critical work, use Tier 1 persistent workers |
| Memory spike from concurrent async + persistent | Monitor with `scripts/status.sh`; cap total concurrency | Backend memory cap (e.g., vLLM `--gpu-memory-utilization`) leaves headroom |
| Backend-specific quirks (SGLang sm121, Ollama context limits) | Document in backend-specific config files, not in core integration | Keep integration model-agnostic; push backend notes to `config/<backend>/` |

---

## 8. Success Criteria

- [ ] `scripts/async-dispatch.sh` dispatches N background subagents to any local backend and returns handles
- [ ] `scripts/status.sh` shows active async subagents alongside tmux workers and hardware metrics (if available)
- [ ] Orchestrator SOUL.md correctly handles `async-burst` tagged kanban tasks on any backend
- [ ] `scripts/smoke-kanban-flow.sh` passes with async-burst task flow
- [ ] `scripts/benchmark-async.sh` produces artifact bundle comparing async vs persistent:
  - [ ] Async: N subagents, wall time, tok/s, memory delta
  - [ ] Persistent: N tmux workers, wall time, tok/s, memory delta
- [ ] README and SKILL.md accurately describe model-agnostic two-tier concurrency
- [ ] No existing tmux/kanban functionality broken (100/100 grade maintained)
- [ ] Backend stays stable during mixed async + persistent load (user-verified with their specific backend)

---

## 9. Quick Reference: Async Subagent API

```python
# From orchestrator session (profile: orchestrator)
# All subagents hit the backend configured in ~/.hermes/profiles/orchestrator/config.yaml

delegate_task(
    goal="Research the model architecture and throughput",
    context="Focus on: MoE design, quantization, backend compatibility. Report specific tok/s numbers if available. Backend is at the endpoint configured in this profile.",
    toolsets=["web"],
    background=True,  # ← Runs async, returns immediately, result re-enters later
)

# Result re-enters conversation as a new user message when complete:
# "Async delegation <delegation_id> completed: <summary>"
# The orchestrator synthesizes multiple such results and updates kanban
```

---

*Plan authored for r0b0tlab/hermes-concurrent-agents. Model-agnostic. Ready for implementation.*
