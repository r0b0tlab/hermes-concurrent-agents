# Hermes Concurrent Agents Modernization Implementation Plan

> **For Hermes:** Use the `subagent-driven-development` skill to implement this plan task-by-task. Preserve the tmux/process/profile/workspace isolation contract throughout.

**Goal:** Modernize `hermes-concurrent-agents` into a resilient, human-friendly control plane optimized **first for NVIDIA GB10 (single box) and GB10 clusters**, secondarily usable on other large-memory devices. A human submits a large task; HCA runs many isolated local Hermes sessions against high-throughput local inference under adaptive unified-memory backpressure; the operator can watch every live session; crashes reclaim cleanly; throughput is measured on real GB10 hardware — without KV/session collisions, dual dispatchers, or opaque tmux guesswork.

**Primary platforms:** single DGX Spark / GB10; multi-node Spark cluster (2-node QSFP direct, 3-node ring, or N-node via QSFP switch — per NVIDIA playbooks); secondarily other large-memory Linux hosts.  
**Secondary platforms:** other Linux unified-memory / high-VRAM hosts.  
**Tertiary (compat only):** macOS/Apple Silicon and generic laptops for CLI/dev/CI — not the performance target.

**Authoritative GB10 ops reference:** [NVIDIA/dgx-spark-playbooks](https://github.com/NVIDIA/dgx-spark-playbooks) (connect-two-sparks, connect-three-sparks, multi-sparks-through-switch, vllm, sglang, hermes-agent, connect-to-your-spark, nccl, tailscale). HCA must **compose with** these playbooks, not reinvent networking/inference install paths.

**Success looks like:**
- On one GB10: `hca up` + a large swarm runs under measured concurrency with live `hca watch`.
- On a GB10 cluster: tasks place onto nodes by capacity; each node keeps tmux/process isolation; no NFS-shared SQLite; human sees fleet-wide status.
- Crashes reclaim cleanly; throughput and knee points come from GB10 benchmark artifacts, not folklore.

**Architecture:** Hermes Kanban remains the durable task/state authority **per control plane**. A small dependency-light Python **node supervisor** replaces fragile shell orchestration and calls Hermes' dispatcher through one quarantined compatibility module, supplying a tmux-backed spawn function so every active task runs as a separate Hermes OS process in its own durable tmux slot, profile, session, and workspace/worktree **on the node that owns the run**. A pip-installed Hermes plugin provides global (node-local, and cluster-aggregated) delegation admission control **and** human-readable session activity telemetry. An observability plane composes Kanban runs, Hermes sessions, tmux output, and plugin events into one operator view. Inference is primarily **node-local OpenAI-compatible servers**. **vLLM and SGLang are first-class equal backends** (same HCA integration surface: endpoint, model id, metrics adapter, health checks, capacity signals); operators pick one per node/pool via preset. Continuous batching and prefix caching raise aggregate throughput without merging agent state. Cluster mode adds a thin **placement + capacity plane** on top of identical node supervisors — not a second agent framework.

**Tech Stack:** Python 3.11+ stdlib-first; Hermes Agent >= verified minimum (initial target installed `v0.18.2 / 2026.7.7.2`); tmux; SQLite/WAL (node + single-host control); **cluster fabric = passwordless SSH (OpenSSH)** as the default GB10-cluster transport (inventory of hosts, remote `hca`/`tmux`/`doctor` over `ssh`); optional HTTP node helper only if SSH is unavailable; Hermes Kanban; profile distributions; Git worktrees; **first-class vLLM + SGLang backend packs** (compose/launch, metrics, doctor probes); optional Ollama/other OpenAI-compatible as secondary; pytest/Ruff/mypy; CI on Linux (primary) + macOS (compat).

---

## 1. Audit Findings That Drive This Plan

This plan is based on a full inventory of the repository, the current official docs tree, and the installed up-to-date Hermes source/runtime.

### 1.1 Existing repository strengths

- The repository already targets concurrent local agents and has GB10-oriented research/tuning notes.
- It correctly identifies the main performance opportunity: many independent agent requests can share one continuously batching local model server — the right shape for GB10 unified memory.
- It already uses Hermes profiles, Kanban, tmux, checkpoints, backend checks, benchmark artifacts, and fault-injection concepts.
- `scripts/smoke-kanban-flow.sh` passes against the installed Hermes build.
- Static docs validation, benchmark dry-run, and shell syntax checks pass.
- Model choice is still operator-selected; orchestration should stay model-flexible while **shipping GB10-first backend presets and capacity defaults**.

### 1.1b Deployment posture (new)

| Tier | Hardware | Role in product |
|---|---|---|
| **P0** | Single DGX Spark / GB10 | Default path; full feature + performance claims |
| **P0** | Spark cluster (2+ nodes) | First-class; networking via NVIDIA QSFP playbooks + passwordless SSH |
| **P1** | Other Linux high-memory / multi-GPU hosts | Same node agent; generic backend adapters |
| **P2** | macOS / laptops | Dev, CLI, unit/integration; not perf authority |

Implication: documentation, defaults, benchmarks, and CI performance gates center on Linux/GB10. macOS remains compatibility, not the product story.

### 1.1c NVIDIA playbook alignment (required reading for implementers)

HCA inherits operational truth from NVIDIA DGX Spark playbooks rather than inventing Spark-specific networking or server installs:

| Concern | NVIDIA playbook | HCA implication |
|---|---|---|
| Laptop → Spark SSH / mDNS | `connect-to-your-spark` | Single-node remote ops; tunnel dashboards with `ssh -L` |
| 2-node 200GbE QSFP + passwordless SSH | `connect-two-sparks` (+ `discover-sparks`) | **Prerequisite** before `hca cluster` on 2 Sparks; same username on all nodes |
| 3-node ring | `connect-three-sparks` | Same; use NVIDIA `spark_cluster_setup` scripts where offered |
| N-node via switch | `multi-sparks-through-switch` | Same at scale; head-node SSH to all nodes |
| Multi-node GPU collectives | `nccl` | Required only if using multi-node **sharded** serving (vLLM Ray/TP), not for default per-node agent fleets |
| vLLM serve | `vllm` | First-class engine; Docker ARM64/Blackwell recipes; OpenAI `/v1`; multi-node TP optional |
| SGLang serve | `sglang` | First-class engine; `lmsysorg/sglang:latest-cu130`; port **30000** typical |
| Hermes + local model | `hermes-agent` | Validates Hermes↔vLLM local path; security: bind models to localhost unless intentionally shared |
| Remote access mesh | `tailscale` | Optional for off-LAN operator access; not a substitute for QSFP cluster fabric |
| UMA memory pressure | Notes in vLLM/SGLang/Hermes playbooks | Prefer admission control; optional guided `drop_caches` only as documented recovery, never automatic silent |

**Two distinct multi-node modes (do not conflate):**
1. **HCA agent fleet (default):** each Spark runs its own vLLM or SGLang + local Hermes workers. Scale = more concurrent agent sessions. Networking = passwordless SSH for control plane.
2. **Sharded model serve (optional):** one logical model spanning Sparks via vLLM multi-node / NCCL (NVIDIA “Run on two Sparks” inference path). Scale = larger single model. HCA may point all workers at that one endpoint, but does not own NCCL/Ray setup — point operators at NVIDIA playbooks.

### 1.2 Critical gaps

1. **The tmux fleet is not actually integrated with the current Kanban worker lifecycle.**
   - `scripts/spawn.sh` opens interactive Hermes sessions and sends a prose briefing with `tmux send-keys`.
   - Current Hermes Kanban workers are spawned by the dispatcher with task-scoped environment variables and dedicated `kanban_*` tools.
   - A persistent interactive worker that was not started with `HERMES_KANBAN_TASK`, `HERMES_KANBAN_RUN_ID`, board, lock, and workspace variables does not have the same lifecycle contract.
   - Result: the tmux pool and the gateway dispatcher can become two unrelated execution systems.

2. **The repository documents stale subagent semantics.**
   - The untracked `INTEGRATION_PLAN.md` and related skill notes assume `max_async_children`, single-task-only background delegation, per-call toolsets/model selection, and manual `background=true` as the primary API.
   - The installed runtime dynamically describes `delegate_task` as background fan-out with batch support, one global `delegation.max_concurrent_children` cap, config-level model/provider routing, and no model-facing per-call toolset/model selection.
   - The public docs page still describes a synchronous fork/join contract, while the installed up-to-date runtime schema/source exposes background completion delivery. This mismatch must be handled by runtime contract detection, not by hard-coded prose.

3. **Profile isolation is incomplete for a real concurrent pool.**
   - One profile per role isolates roles, but two simultaneous tasks assigned to the same role still share that role's `HERMES_HOME`, memory, session database, plugin state, and normal host `HOME`.
   - Profiles are state boundaries, not filesystem sandboxes. Worktrees/workspaces are separate and must be enforced independently.

4. **`--continue` is unsafe as a generic crash-recovery primitive.**
   - It resumes the most recent CLI session for a profile, which may belong to another task when one role runs concurrently.
   - Recovery must use exact session IDs when continuation is intended, or start a fresh run with Kanban's prior-run context.

5. **The current shell control plane is not cross-platform or sufficiently stateful.**
   - `scripts/health-monitor.sh --once` fails on macOS because it requires GNU `free`.
   - `status.sh` also assumes Linux memory commands.
   - Shell parsing of YAML, JSON, tmux state, process identity, resource leases, and Kanban runs will become increasingly fragile.

6. **Current safety/performance claims are too broad.**
   - tmux does not isolate backend KV cache by itself. The real boundaries are: separate Hermes process + exact session + profile for agent state, workspace/worktree for files, and backend admission control for KV memory.
   - A fixed “3 workers is optimal” cannot be a portable default. The sweet spot depends on model, quantization, context lengths, backend scheduler, prompt mix, and memory headroom.
   - A fixed “64K is Hermes minimum” should be removed unless current Hermes explicitly validates it. Context should be discovered from the model/backend and configured from measured requirements.

7. **The tests validate scripts more than behavior.**
   - Fault injection is dry-run by default and does not prove supervisor restart, duplicate-spawn prevention, exact session recovery, tmux/PID reconciliation, worktree isolation, or global subagent backpressure.
   - CI has no macOS job despite claiming Apple Silicon/macOS compatibility.

### 1.3 Existing plan handling

`/Users/am/hermes-concurrent-agents/INTEGRATION_PLAN.md` is untracked and materially stale. Do not silently overwrite or commit it. After this plan is implemented, either delete it with explicit approval or replace it with a short pointer to this document.

---

## 2. Isolation Model and Non-Negotiable Invariants

### 2.1 Isolation comparison

| Layer | Required boundary | Purpose | What it does not guarantee |
|---|---|---|---|
| Durable tmux **slot** | One attachable session per worker capacity unit (`hca-<fleet>-<role>-NN`) | Detach/attach, inspectability, host-local durability across controller restart | Filesystem sandboxing or GPU KV partitioning |
| OS process (run) | One Hermes process per admitted Kanban **run** inside a slot | Independent Python/client/tool/session runtime | Separate model weights when all call one backend |
| Hermes profile instance | One `HERMES_HOME` per slot (`hca.<fleet>.coder-01`, …) | Config, memory, sessions, skills, plugins, logs | Host filesystem or CLI credential isolation by default |
| Hermes session | Fresh session per independent run; exact ID only for intentional resume | Prevent conversation/context crossover | File-write isolation |
| Workspace/worktree | Dedicated task directory; worktree for code changes | Prevent agents editing the same checkout | Provider/backend isolation |
| Kanban board/run | Durable task, dependency, retry, heartbeat, evidence trail | Source of lifecycle truth | Multi-host consensus |
| Resource lease | Shared HCA SQLite admission ledger | Bound total parent + subagent backend pressure | Hard GPU partitioning |
| Inference backend | Shared endpoint with continuous batching | Reuse weights and maximize aggregate throughput | Unlimited KV memory |

**Slot vs run:** a slot is a durable capacity container (tmux + profile). A run is one attempt. At most one active run per slot. The controller recreates missing slots; it never treats “tmux exists” as “worker healthy.”

### 2.2 Core invariants

1. Exactly one live slot and one worker PID may own an active Kanban run.
2. **Admit before claim.** Ready tasks stay ready until a free slot, healthy backend, and resource lease exist. Never claim then wait on capacity.
3. A run never starts without board, task ID, run ID, claim lock, concrete profile slot, absolute workspace, and recorded launch manifest.
4. Gateway-embedded Kanban dispatch is disabled while HCA's tmux supervisor is active; two dispatchers must never race on one board.
5. Every worker terminates through `kanban_complete` or `kanban_block`; a normal process exit without either is a protocol violation.
6. `--continue` is never used. Resume only by exact recorded Hermes session ID when all ownership checks match; otherwise start a fresh run with prior-run context.
7. Code-changing tasks default to worktrees; never combine Kanban worktree allocation with `hermes -w`; agents never share a writable checkout.
8. Subagent fan-out consumes the same global resource budget as top-level tmux workers and must be reserved up front.
9. Nested delegation is disabled by default. Kanban is the durable hierarchy; `delegate_task` is only a transient reasoning burst.
10. No API key or secret appears in process arguments, tmux names, state DB fields, benchmark manifests, or logs.
11. The control plane is **deterministic code** (no LLM). The orchestrator profile may create/link Kanban tasks but is structurally unable to implement work (no implementation toolsets).
12. **GB10-first, portable second.** Core node protocol is reusable; defaults, presets, metrics adapters, and published numbers are GB10-oriented. Other hardware works via the same node agent with different presets.
13. **No NFS/shared-SQLite multi-host Kanban.** Cluster mode uses one control-plane Kanban owner + per-node supervisors reached primarily over **passwordless SSH** (standard on GB10 clusters). Optional HTTP node helper is secondary only.
14. Unknown non-fleet tmux sessions are never killed automatically. Duplicate ownership is quarantined and surfaced, not “fixed” by guessing.
15. **Wave release:** a fan-out that creates many ready children is admitted only up to capacity; excess stay ready with explicit wait reasons (never claim-and-queue on GPU).
16. **Warm slots at `hca up`:** create/reconcile empty durable slots and profile homes before the first task, so dispatch latency is process-start, not profile/tmux bootstrap.
17. **Unattended safety:** fleet default is non-interactive approvals policy appropriate for automation (`--yolo` only when operator explicitly configures it); destructive tools remain role-scoped.
18. **Disk is a resource:** run logs, peeks, transcripts, worktrees, and checkpoints have retention caps and fail admission on disk high-watermark.
19. **Colocate agent run with inference when possible.** Default placement prefers the node that hosts the model endpoint for that task (or a dedicated inference pool explicitly configured). Cross-node chat traffic is opt-in, measured, and visible.
20. **Cluster scale-out is horizontal nodes, not bigger shared SQLite.** Each GB10 remains an isolation domain for tmux/process/profile/workspace.

---

## 3. Target User Experience

```bash
# One-time initialization
hca init --backend http://127.0.0.1:8000/v1 --model served-model
hca doctor

# Start one fleet supervisor in a dedicated tmux control session
hca up

# Submit a large task (examples)
hca task add "Implement issue 123" --role coder --repo ~/src/app --goal
hca task add "Research three options" --role research --workspace ~/work/research
hca task swarm "Ship the release" --workers research,coder,qa --goal
hca plan --dry-run t_root     # estimated credits, waves, slot demand (no claim)

# Human control + observability
hca status                 # logical task/run view (+ root progress rollup)
hca ps                     # physical slot/process view
hca watch                  # live multi-session mission control (auto-refresh)
hca explain t_abcd         # exact backpressure / scheduler reason
hca peek t_abcd            # latest pane snapshot without attaching
hca logs t_abcd --follow   # run log stream (tools, state, stderr)
hca activity --follow      # fleet-wide event stream (starts/tools/blocks/completes)
hca transcript t_abcd      # Hermes conversation transcript for that run/session
hca attach t_abcd          # full interactive tmux attach (explicit opt-in)
hca task comment t_abcd "Use the v2 schema"
hca task retry t_abcd
hca dashboard              # open Hermes Kanban UI for the board

# Safe shutdown
hca drain          # stop admitting new work; let active runs finish
hca down           # graceful drain; preserve tmux unless explicit
hca down --kill    # explicit emergency termination
```

**Single GB10 (default):**
```bash
# Pick one first-class backend
hca init --preset gb10-vllm --backend http://127.0.0.1:8000/v1 --model <served>
# or
hca init --preset gb10-sglang --backend http://127.0.0.1:30000/v1 --model <served>

hca doctor
hca up
hca task swarm "Ship the release" --workers research,coder,qa --goal
hca watch
```

**GB10 cluster (passwordless SSH assumed — after NVIDIA connect playbook):**
```bash
# 0) One-time physical/network/SSH setup (NVIDIA, not HCA):
#    2 nodes:  connect-two-sparks (+ discover-sparks)
#    3 nodes:  connect-three-sparks
#    N nodes:  multi-sparks-through-switch
# Requires: same username on all Sparks; QSFP fabric up; passwordless SSH both ways.

# On control Spark — inventory cluster fabric hosts (prefer CX7 / QSFP IPs)
hca cluster init --preset gb10-cluster-vllm   # or gb10-cluster-sglang
hca cluster nodes add gb10-a gb10-b gb10-c    # hostnames or link-local/static QSFP IPs
hca cluster doctor                            # BatchMode ssh, same-user, hermes, tmux, backend
hca up --role control

# Ensure node supervisors (idempotent over SSH)
hca cluster nodes up

# Submit on control; placement SSHes to chosen nodes
hca task swarm "Cluster-scale research + implement" --goal
hca watch --cluster

# Observe remote runs over SSH
hca peek t_abcd
hca attach t_abcd
hca logs t_abcd --follow
```

No join tokens or extra cluster auth plane by default — reuse passwordless SSH established by NVIDIA `discover-sparks` / `ssh-copy-id` flows (`BatchMode=yes`, optional `ControlMaster`).

Four user-visible nouns only: **fleet**, **slot**, **task**, **run**.  
Cluster adds: **node** (a GB10/host running a node supervisor).

### 3.1 Human observability requirements

A human must answer these questions in under 10 seconds without raw `tmux` knowledge:

1. What is running right now across the fleet?
2. What is **this** agent doing (current tool, last model reply, goal progress)?
3. Is it healthy, stuck, waiting on capacity, blocked on a human, or failed?
4. What did it decide/write, and where are the artifacts?
5. How do I inspect without disturbing it, and how do I intervene when needed?

**Observation modes (from least to most intrusive):**

| Mode | Command | Intrudes on agent? | Use when |
|---|---|---|---|
| Summary | `hca status` / `hca ps` | No | Fleet overview |
| Live board | `hca watch` | No | Continuous monitoring |
| Why waiting | `hca explain` | No | Backpressure / block diagnosis |
| Pane peek | `hca peek` | No (read-only capture) | Quick visual of latest output |
| Event stream | `hca activity --follow` | No | Tool/lifecycle timeline |
| Run logs | `hca logs --follow` | No | Persistent structured log |
| Transcript | `hca transcript` | No | Full conversation for a run |
| Interactive | `hca attach` | Yes (shared terminal) | Manual intervention |
| Dashboard | `hca dashboard` | No | Kanban board UX |

**Live row fields (status/ps/watch):** board, task, run, role, slot, Kanban state, tmux session, PID, Hermes session ID, workspace/worktree/branch, backend, elapsed, heartbeat age, parent/subagent leases, **current activity** (last tool or model phase), **progress summary** (from todo/goal/run events when available), backpressure/block reason, last error.

**Root-goal rollup:** when viewing a parent/goal task, `status`/`watch` show child counts by state (`ready/running/blocked/done/failed`), estimated remaining credits, and oldest blocker — so a large job is operable without opening every child.

**Privacy/safety for observability:**
- Redact secrets and env values in peeks, logs, activity, and transcripts.
- Never print API keys, claim locks, or full `.env` contents.
- Observation defaults to non-interactive; attach is explicit.
- Multiplexed views must not inject keystrokes into worker panes.

---

## 4. Proposed Repository Layout

```text
hermes-concurrent-agents/
├── pyproject.toml
├── src/hca/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── doctor.py
│   ├── hermes_compat.py
│   ├── kanban.py
│   ├── models.py
│   ├── profiles.py
│   ├── resources.py
│   ├── state.py
│   ├── supervisor.py
│   ├── telemetry.py
│   ├── observe.py
│   ├── events.py
│   ├── tmux.py
│   ├── plugin.py
│   ├── cluster.py                 # placement, node inventory, SSH transport
│   ├── ssh_exec.py                # passwordless SSH runner (BatchMode, multiplex)
│   ├── node_remote.py             # remote hca/tmux/status over SSH
│   ├── presets.py
│   └── backends/
│       ├── __init__.py
│       ├── openai_compat.py       # shared health / tool-call / models probe
│       ├── vllm.py                # vLLM metrics + capacity signals
│       └── sglang.py              # SGLang metrics + capacity signals
├── profiles/
│   ├── orchestrator/{distribution.yaml,config.yaml,SOUL.md}
│   ├── coder-worker/{distribution.yaml,config.yaml,SOUL.md}
│   ├── research-worker/{distribution.yaml,config.yaml,SOUL.md}
│   ├── qa-worker/{distribution.yaml,config.yaml,SOUL.md}
│   └── creative-worker/{distribution.yaml,config.yaml,SOUL.md}
├── config/
│   ├── hca.example.toml
│   ├── presets/
│   │   ├── gb10-vllm.toml
│   │   ├── gb10-sglang.toml
│   │   ├── gb10-cluster-vllm.toml
│   │   ├── gb10-cluster-sglang.toml
│   │   └── generic-linux.toml
│   └── backends/
│       ├── vllm/
│       │   ├── docker-compose.yml
│       │   └── README.md
│       ├── sglang/
│       │   ├── docker-compose.yml
│       │   ├── launch.sh
│       │   └── README.md
│       └── ollama/README.md       # secondary only
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── e2e/
│   └── fixtures/fake_openai_server.py
├── scripts/
│   ├── hca
│   └── release-check.sh
├── docs/
│   ├── architecture.md
│   ├── gb10.md
│   ├── gb10-cluster.md            # NVIDIA topology + HCA SSH placement
│   ├── backends-vllm-sglang.md    # equal first-class; cite NVIDIA playbooks
│   ├── nvidia-playbooks.md        # index of required upstream playbooks
│   ├── isolation-and-kv-cache.md
│   ├── subagent-policy.md
│   ├── observability.md
│   ├── operations.md
│   ├── migration-v1-to-v2.md
│   ├── benchmarking.md
│   ├── troubleshooting.md
│   └── plans/2026-07-12-hermes-agent-modernization.md
└── .github/workflows/{ci.yml,hardware-benchmark.yml}
```

---

## 4.1 Deployment topologies (GB10-first)

### A. Single GB10 (P0 — default)

```text
[Human]
   │
   ▼
hca CLI / watch / dashboard
   │
   ▼
┌─────────────── GB10 node ────────────────┐
│ HCA supervisor (control+node combined)   │
│ Kanban SQLite (local disk)               │
│ tmux slots → Hermes processes            │
│ worktrees / artifacts (local or NFS* )   │
│        │                                 │
│        ▼                                 │
│  vLLM *or* SGLang (first-class equal)    │
│  OpenAI-compatible /v1                   │
│  unified-memory KV + continuous batching │
└──────────────────────────────────────────┘
```

\*NFS for **git/artifacts** is optional and separate from Kanban DB. Never put Kanban SQLite on NFS.

### B. GB10 / Spark cluster (P0 — first-class, NVIDIA fabric + passwordless SSH)

```text
[Human / laptop] --SSH/mDNS/Tailscale--> Control Spark
                                            │ Kanban (local disk)
                                            │ Placement
                                            │ ssh BatchMode to fabric IPs
                   ┌────────────────────────┼────────────────────┐
                   ▼                        ▼                    ▼
              Spark A                  Spark B              Spark C
              hca node                 hca node             hca node
              tmux + Hermes            tmux + Hermes        ...
              vLLM:8000 or             vLLM or SGLang
              SGLang:30000             (per-node default)
```

**Prerequisites (NVIDIA playbooks — HCA assumes these are done):**
1. Physical topology one of: 2-node QSFP direct, 3-node ring, or N-node QSFP switch ([connect-two-sparks](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-two-sparks), [connect-three-sparks](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-three-sparks), [multi-sparks-through-switch](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/multi-sparks-through-switch)).
2. **Same username on all Sparks** (NVIDIA hard requirement).
3. CX7 interfaces configured (netplan / `spark_cluster_setup`); prefer `enp1s0f*` names; verify with `ibdev2netdev`.
4. **Passwordless SSH** both ways via `discover-sparks` or `ssh-copy-id`.
5. Latest OS/firmware per DGX Spark docs when multi-node.

**HCA rules on top:**
1. **One Kanban owner** (control Spark). Nodes do not share the SQLite file / no NFS for Kanban.
2. Each node runs the **same** node supervisor: admit → tmux spawn → observe.
3. **Cluster transport default = passwordless SSH** over the cluster fabric IPs (or hostnames once resolvable). Do not invent join-token auth as primary.
4. Node inventory is declarative hosts; prefer the **QSFP/CX7 addresses** used in NVIDIA setup, not only LAN Wi‑Fi/Ethernet, for inter-node control traffic when fabric exists.
5. Capacity discovery is **pull over SSH**: `ssh node hca node status --json` on an interval; optional `ControlMaster` multiplexing.
6. Placement colocates Hermes workers with that node’s vLLM/SGLang by default.
7. Workspaces: node-local worktrees; multi-node code via git remotes + per-node worktrees.
8. Observability: remote `peek`/`logs`/`attach` via SSH (`ssh -t` for attach).
9. Failure: SSH fail → node unhealthy → reclaim/requeue.
10. **SSH hygiene:** `BatchMode=yes`, timeouts, no password prompts, never log keys.
11. Optional Tailscale for **operator remote access** to control node; not a replacement for QSFP cluster fabric.
12. Inference HTTP stays on each node (vLLM **8000**, SGLang **30000** per NVIDIA defaults). SSH is orchestration only.
13. **Optional mode — multi-node sharded serve:** if operators follow NVIDIA vLLM “Run on two/multiple Sparks” (Ray/NCCL), HCA may treat that as a single remote OpenAI endpoint. Document as advanced; default remains per-node engines for agent concurrency.

### C. Secondary non-GB10 host

Same single-node path with `presets/generic-linux.toml` and lower default capacity. No special-case code paths required beyond metrics adapters.

---

## 4.2 Proposed Repository Layout (continued)

Retain old shell commands as deprecation wrappers for one release, but move all stateful logic into Python.

---

## 5. Implementation Tasks

### Task 1: Freeze the baseline and create an executable compatibility contract

**Objective:** Record what currently works, what fails, and which Hermes runtime behavior HCA depends on.

**Files:**
- Create: `docs/audit-2026-07-12.md`
- Create: `tests/contract/test_hermes_runtime.py`
- Create: `tests/contract/test_kanban_contract.py`
- Modify: `CHANGELOG.md`

**Steps:**
1. Record the installed Hermes version, source commit, docs URLs, current repository commit, and baseline commands.
2. Add contract tests for:
   - `hermes --version` is parseable and meets the declared minimum.
   - `hermes config check` succeeds.
   - Kanban supports boards, runs, diagnostics, worktrees, goals, skills, idempotency keys, max runtime/retries, swarm, and structured summaries.
   - `hermes_cli.kanban_db.dispatch_once` accepts a `spawn_fn` and returns the expected result fields.
   - the active `delegate_task` schema is inspected at runtime for batch/background/role semantics instead of inferred from version alone.
3. Mark the docs/runtime delegation mismatch as a known compatibility hazard.
4. Add a test that fails with an actionable message when the private Kanban adapter signature changes.

**Verification:**
```bash
pytest -q tests/contract
```
Expected: all tests pass on the supported Hermes build; unsupported builds fail before any worker is spawned.

**Commit:** `test: pin Hermes orchestration contracts`

### Task 2: Add the dependency-light Python package and CLI skeleton

**Objective:** Replace stateful Bash orchestration with a typed, testable CLI while keeping installation simple.

**Files:**
- Create: `pyproject.toml`
- Create: `src/hca/__init__.py`
- Create: `src/hca/cli.py`
- Create: `src/hca/models.py`
- Create: `tests/unit/test_cli.py`
- Modify: `scripts/spawn.sh`, `scripts/status.sh`, `scripts/shutdown.sh` into warning wrappers

**Steps:**
1. Expose a console script: `hca = hca.cli:main`.
2. Implement argparse subcommands: `init`, `doctor`, `up`, `drain`, `down`, `status`, `ps`, `watch`, `explain`, `peek`, `logs`, `activity`, `transcript`, `inspect`, `attach`, `dashboard`, `task`, `plan`, `bench`, `cluster` (nodes add/list/up/doctor/probe).
3. Keep runtime dependencies in the standard library where practical; use optional extras only for richer hardware telemetry.
4. Make every mutating command support `--dry-run` and `--json`.
5. Make errors machine-readable and human-readable; use stable exit codes.
6. Replace legacy scripts with wrappers that point users to equivalent `hca` commands for one compatibility release.

**Verification:**
```bash
python -m pip install -e .
hca --help
pytest -q tests/unit/test_cli.py
```

**Commit:** `feat: add hca control-plane CLI`

### Task 3: Define fleet configuration and durable control state

**Objective:** Create one explicit source of configuration and one reconciliation ledger without competing with Kanban lifecycle truth.

**Files:**
- Create: `config/hca.example.toml`
- Create: `src/hca/config.py`
- Create: `src/hca/state.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/test_state.py`

**Configuration sections:**
- `[fleet]`: name, board(s), tmux socket, dispatch interval, launch stagger, drain policy, warm-slot policy, max wave size, role (`single`|`control`|`node`).
- `[backend]`: **engine** (`vllm`|`sglang`|`openai_compat`), endpoint, model, API mode, local-only policy, metrics path/port, optional auxiliary endpoint for compression/title.
- `[capacity]`: max top-level runs, max total estimated sequences, memory/disk high/low watermarks, per-role caps, reserve lane for retries/control; GB10 preset seeds from measured defaults.
- `[cluster]`: node inventory (host, ssh_user?, ssh_port?, labels, fabric_ip?), transport=`ssh` (default) | `http` (optional), probe interval, placement policy (`colocate-infer` default), ssh multiplex options, connect/command timeouts, **require_same_username=true**. **No join token required for SSH mode.** Prerequisite note: NVIDIA connect-* playbook completed.
- `[profiles]`: role templates and number of isolated slots per role.
- `[workspace]`: default workspace mode by role, worktree root, artifact retention.
- `[delegation]`: transient burst limit, nesting policy, lease TTL, optional resident secondary endpoint.
- `[approvals]`: unattended policy (default safe automation posture; explicit opt-in to auto-approve dangerous commands).
- `[telemetry]`: local JSONL/SQLite paths and retention; no outbound telemetry.
- `[observe]`: watch refresh interval, peek line count, activity retention, transcript source preference (`hermes-session` first, run log fallback), secret redaction patterns, notification targets (optional gateway/local only).
- `[retention]`: max log bytes per run, activity retention days, completed-run log TTL, worktree retain-until policy.

**State DB rules:**
- Store only HCA control mappings: fleet, board, task, run, profile slot, tmux session, PID, Hermes session ID, resource lease, timestamps, and sanitized errors.
- Store a compact **activity cursor** / last-known activity summary for live status (not full transcripts).
- Use SQLite WAL, foreign keys, unique constraints on live `(board, run_id)` and profile-slot leases.
- Kanban remains authoritative for task state; HCA state is rebuildable by reconciliation.
- Full conversation history lives in Hermes session store; HCA only stores IDs and pointers.

**Verification:**
```bash
pytest -q tests/unit/test_config.py tests/unit/test_state.py
```

**Commit:** `feat: add fleet config and reconciliation state`

### Task 4: Convert role templates into Hermes profile distributions

**Objective:** Use current Hermes profile installation/update semantics instead of manual profile-directory copying.

**Files:**
- Create/modify: `profiles/*/distribution.yaml`
- Create/modify: `profiles/*/config.yaml`
- Modify: `profiles/*/SOUL.md`
- Create: `src/hca/profiles.py`
- Create: `tests/integration/test_profiles.py`
- Replace: `setup.sh` with compatibility wrapper

**Steps:**
1. Add a distribution manifest and Hermes minimum version to every role.
2. Add precise role descriptions so Kanban auto-decomposition can route correctly.
3. Install N **slot profiles** per role, e.g. `hca-coder-01`, `hca-coder-02`, each with its own `HERMES_HOME`.
4. Preserve local profile config on updates; provide explicit `--reset-profile-config` rather than broad `--force`.
5. Enable only required toolsets per role:
   - orchestrator: Kanban/control/memory only; no terminal/file/web implementation surface.
   - coder: terminal/file/GitHub as configured.
   - research: web/file; terminal only when necessary.
   - QA: file/terminal/verification; no publishing.
   - creative: file/media as configured.
6. Set `terminal.cwd` per task at launch, not globally to `.`.
7. Default `terminal.home_mode` to host for normal CLI credentials; document strict profile-home mode as opt-in.
8. Configure checkpoints with size/retention caps, not only snapshot count.
9. Configure `delegation.max_spawn_depth: 1` and `orchestrator_enabled: false` by default for fleet workers.

**Verification:**
```bash
hca init --dry-run --backend http://127.0.0.1:8000/v1 --model test-model
pytest -q tests/integration/test_profiles.py
```

**Commit:** `feat: distribute isolated worker-slot profiles`

### Task 5: Implement exact tmux process management

**Objective:** Give every active task run an isolated, attachable tmux envelope with deterministic identity.

**Files:**
- Create: `src/hca/tmux.py`
- Create: `tests/unit/test_tmux.py`
- Create: `tests/integration/test_tmux_real.py`

**Steps:**
1. Use a fleet-specific tmux socket (`tmux -L <fleet>`) and durable slot session names: `hca-<fleet>-<role>-NN` (sanitized; **no colons** — `:` breaks tmux target syntax).
2. On `hca up`, ensure all configured slots exist (warm pool) even when idle; idle slots have no Hermes child or a sleeping placeholder process policy that does **not** hold model context.
3. Keep slots alive across controller restart; restart the Hermes **child process** per run, not the slot identity.
4. Start the server with `remain-on-exit` so completed/crashed panes remain inspectable until retention cleanup.
5. Start workers with a sanitized explicit environment allowlist; never copy arbitrary secrets into tmux metadata.
6. Launch each run with `exec` so `#{pane_pid}` is the Hermes process PID used by Kanban crash detection.
7. Capture output using `tmux pipe-pane` into board/run-specific logs without changing the worker PID. Also support non-destructive `capture-pane` for `hca peek`.
8. Implement readiness based on process existence and explicit lifecycle state, not fixed sleeps or prompt text scraping.
9. Implement `attach`, `read`/`peek`, `signal`, `terminate`, and garbage collection without raw `send-keys` for task dispatch.
10. Refuse non-fleet session collisions rather than killing unrelated tmux sessions automatically.
11. Never use pane text scraping as completion signal; Kanban run state + exit protocol are authoritative. Pane capture is **observability only**.
12. For `attach`, document that it is shared-terminal intervention; prefer peek/logs/transcript for read-only inspection.

**Verification:**
```bash
pytest -q tests/unit/test_tmux.py tests/integration/test_tmux_real.py
```

**Commit:** `feat: add isolated tmux run manager`

### Task 6: Build the Hermes Kanban compatibility adapter

**Objective:** Reuse Hermes' atomic claims, workspaces, run records, retries, and lifecycle while containing private-API coupling in one module.

**Files:**
- Create: `src/hca/hermes_compat.py`
- Create: `src/hca/kanban.py`
- Create: `tests/contract/test_hermes_compat.py`
- Create: `tests/integration/test_tmux_spawn.py`

**Steps:**
1. Import Hermes Kanban only through `hermes_compat.py`.
2. Call `dispatch_once(..., spawn_fn=hca_tmux_spawn)` so Hermes performs reclaim, promotion, atomic claim, run creation, limits, and task context.
3. Reproduce the current default worker environment contract exactly:
   - `HERMES_HOME`, `HERMES_PROFILE`
   - `HERMES_KANBAN_TASK`, `HERMES_KANBAN_RUN_ID`, `HERMES_KANBAN_CLAIM_LOCK`
   - `HERMES_KANBAN_BOARD`, `HERMES_KANBAN_DB`
   - `HERMES_KANBAN_WORKSPACE`, `HERMES_KANBAN_WORKSPACES_ROOT`
   - `HERMES_TENANT`, branch, goal-mode, and timeout variables when present.
4. Launch the same safe worker shape as Hermes core: `hermes -p <slot> --cli --accept-hooks [skills] [model] chat -q "work kanban task <id>"`.
5. Return the exact Hermes PID from tmux to Kanban.
6. Fail closed when the compatibility contract changes; do not silently fall back to shell claims.
7. Document one upstream opportunity: add a stable configurable worker-launcher/spawn hook to Hermes so HCA can stop using the private adapter. Do not block HCA v2 on that upstream change.

**Verification:**
```bash
pytest -q tests/contract/test_hermes_compat.py tests/integration/test_tmux_spawn.py
```

**Commit:** `feat: dispatch Kanban workers through tmux`

### Task 7: Implement the reconciliating supervisor

**Objective:** Make start/restart/crash behavior idempotent and self-healing.

**Files:**
- Create: `src/hca/supervisor.py`
- Create: `tests/unit/test_reconciliation.py`
- Create: `tests/e2e/test_supervisor_restart.py`

**Supervisor loop:**
1. Acquire one fleet leader lock (file/SQLite lock under HCA state; only one controller).
2. Reconcile Kanban runs, HCA state, tmux sessions, and live PIDs.
3. Reclaim expired HCA leases; rotate/truncate logs past retention.
4. Ask Hermes Kanban to reclaim/promote/detect failures.
5. Evaluate resource admission (sequences, memory, disk, role caps, wave size).
6. Dispatch at most the available capacity, with configurable stagger and max-wave limit.
7. Persist mappings, heartbeats, and compact activity headlines for `watch`.
8. Emit operator-visible events for admission waits and recoveries.
9. Repeat with exponential backoff on infrastructure errors.

**Required recovery cases:**
- supervisor dies, workers continue, restarted supervisor adopts them;
- tmux server dies, Kanban detects worker PIDs gone and retries/blocks by policy;
- Hermes exits 0 without Kanban termination, task becomes protocol violation;
- task is manually moved off running, supervisor terminates/reclaims the stale run;
- duplicate `hca up` cannot create a second dispatcher;
- gateway dispatcher enabled on the same board causes `doctor`/`up` to refuse;
- drain stops new dispatch but keeps reconciliation and active workers alive;
- disk high-watermark blocks new runs and surfaces `waiting: disk pressure`;
- large fan-out creates 50 ready children → only capacity-many run; others remain ready with wait reasons.

**Verification:**
```bash
pytest -q tests/unit/test_reconciliation.py tests/e2e/test_supervisor_restart.py
```

**Commit:** `feat: add idempotent fleet supervisor`

### Task 8: Add global resource admission and adaptive backpressure

**Objective:** Protect unified memory and backend KV capacity across top-level runs and subagents — with **equal-class adapters for vLLM and SGLang**.

**Files:**
- Create: `src/hca/resources.py`
- Create: `src/hca/telemetry.py`
- Create: `src/hca/backends/openai_compat.py`
- Create: `src/hca/backends/vllm.py`
- Create: `src/hca/backends/sglang.py`
- Create: `tests/unit/test_resources.py`
- Create: `tests/unit/test_backend_adapters.py`
- Create: `tests/integration/test_backpressure.py`
- Create: `docs/backends-vllm-sglang.md`

**Backend engine contract (identical surface for vLLM and SGLang):**
1. `health()` — `/v1/models` + optional engine health route.
2. `probe_chat()` — tiny completion.
3. `probe_tools()` — tool-calling round trip when required.
4. `capacity()` → normalized signals: `{active_sequences, waiting, kv_cache_util, prefix_hit_rate?, mem_pressure, error_rate, ttft_p95?}`.
5. Missing metrics degrade to conservative sequence-credit accounting — never pretend engine-specific fields exist.

**Policy:**
- Generic baseline: weighted sequence credits, not only “N workers.” Long-context / llm-heavy / subagent-capable parents reserve more.
- Optional task classes: `llm-heavy`, `tool-heavy`, `memory-heavy`, `latency-sensitive`, `batch`.
- **vLLM adapter:** continuous-batching / prefix-cache / scheduler metrics endpoints as available on the running build.
- **SGLang adapter:** scheduler/KV/cache metrics as available on the running build; same normalized `capacity()` shape.
- Engines are **peer alternatives**, not primary/fallback hierarchy. Mixing engines across cluster nodes is allowed; each node reports its engine type in heartbeats.
- High watermark: stop new parent/subagent admission; keep tasks `ready` with explicit reasons.
- Low watermark + hysteresis; increase one step after healthy windows; decrease aggressively on OOM/swap/TTFT/queue/429.
- Never kill useful work solely because a soft watermark was crossed.
- Stagger launches; fairness + aging; reserve lane for retries/control.
- GB10 presets seed conservative starting credits; `hca bench --engine vllm|sglang` overwrites machine-local recommendations.

**Metrics to consider:** active sequences, waiting requests, KV-cache utilization, prefix-cache hit rate, GPU/unified memory, system memory pressure/swap, request error rate, p50/p95 TTFT and inter-token latency, decode TPS, heartbeat age, thermal/power when available.

**Verification:**
```bash
pytest -q tests/unit/test_resources.py tests/unit/test_backend_adapters.py tests/integration/test_backpressure.py
```

**Commit:** `feat: add unified resource governor with vLLM+SGLang adapters`

### Task 9: Add the Hermes subagent budget/telemetry plugin

**Objective:** Make subagent use efficient and globally bounded rather than multiplying independently inside every worker process.

**Files:**
- Create: `src/hca/plugin.py`
- Modify: `pyproject.toml` with `hermes_agent.plugins` entry point
- Create: `tests/unit/test_plugin.py`
- Create: `tests/e2e/test_delegation_budget.py`
- Create: `docs/subagent-policy.md`

**Hook behavior:**
1. `pre_tool_call` on `delegate_task` counts requested batch children and atomically reserves leases in HCA state.
2. If capacity is unavailable, block the call with a message telling the worker to create durable Kanban child cards or continue sequentially.
3. `subagent_start` records child ID, parent session/turn, role, goal hash, and lease; emits a human-visible activity event.
4. `subagent_stop` records status/duration and releases one lease; emits completion/failure activity.
5. `on_session_end` and supervisor TTL reconciliation release orphaned leases after crashes.
6. Additional hooks for observability (best-effort, never fail the agent turn):
   - tool start/end (name + sanitized args summary + duration)
   - turn/model phase markers when available
   - session ID binding for the active Kanban run
   - block/complete signals mirrored from worker Kanban tools when observable
7. Activity events write to local JSONL + compact HCA state “last activity” fields used by `hca watch` / `hca ps`.
8. No-op when `HCA_STATE_DB` is absent, so installing the package does not alter normal Hermes use.
9. Keep callbacks fast and local; no outbound telemetry.

**Default subagent policy:**
- Use a batch `delegate_task(tasks=[...])` for 2-N independent, short, reasoning-heavy subtasks.
- Use `role="leaf"`; nested orchestrators stay off by default.
- Use Kanban child cards for durable, long-running, human-visible, cross-role, retryable, or file-conflicting work.
- Never use subagents for a single tool call or mechanical loops; use direct tools or `execute_code`.
- Treat summaries as unverified claims; parent verifies files/tests/URLs.
- Subagent model/provider is configured per worker profile under `delegation`, not selected ad hoc per call.
- Route to a smaller local model only when it is already resident or served on a separate endpoint; avoid model swapping that destroys cache locality.

**Compatibility rule:** the plugin reads the live tool arguments/schema and supports both documented synchronous and observed background runtimes. `hca doctor` reports which contract is active.

**Verification:**
```bash
pytest -q tests/unit/test_plugin.py tests/e2e/test_delegation_budget.py
```

**Commit:** `feat: govern and observe subagent fan-out`

### Task 10: Enforce workspace, worktree, artifact, and session policies

**Objective:** Eliminate file collisions and ambiguous session recovery.

**Files:**
- Create: `src/hca/workspaces.py`
- Create: `tests/integration/test_worktrees.py`
- Create: `tests/integration/test_session_mapping.py`
- Create: `docs/isolation-and-kv-cache.md`

**Steps:**
1. Default coder/QA code tasks to Kanban `worktree` workspaces with unique branches.
2. Allow `dir:<absolute>` only after canonicalization; reject relative paths.
3. Treat scratch workspaces as ephemeral; require artifacts to be copied to declared durable paths before completion.
4. Record exact Hermes session IDs through the HCA plugin/session hooks.
5. Default retries to a **fresh session** populated by Kanban prior runs/comments.
6. Allow exact `--resume <session_id>` only for an operator-requested continuation of the same task/run lineage.
7. Never use profile-wide `--continue` in supervisor code or docs.
8. Keep AGENTS.md/.hermes.md project context in worktrees; keep SOUL.md concise and role-only to improve prompt/prefix stability.
9. Add branch/worktree cleanup only after task terminal state, no uncommitted changes, and retention expiry.

**Verification:**
```bash
pytest -q tests/integration/test_worktrees.py tests/integration/test_session_mapping.py
```

**Commit:** `feat: enforce task workspace and session isolation`

### Task 11: Integrate current Kanban capabilities into human workflows

**Objective:** Use current Hermes features instead of rebuilding task-management UX.

**Files:**
- Modify: `src/hca/cli.py`
- Create: `tests/integration/test_task_cli.py`
- Modify: `docs/operations.md`

**Expose through `hca task`:**
- board-per-project creation/switching;
- idempotency keys for automation;
- profile-slot/role resolution;
- parent links and fan-out/fan-in;
- `--goal` and completion-contract body templates;
- per-task skills;
- max runtime/retries;
- scheduled starts;
- structured comments and handoff metadata;
- `swarm` graph helper;
- attachments and durable workspace choice;
- notification subscriptions where a gateway target exists.

**Do not duplicate:** the Kanban database, dashboard, decomposer, task status machine, run history, task events, or WebSocket UI.

**Verification:**
```bash
pytest -q tests/integration/test_task_cli.py
```

**Commit:** `feat: expose modern Kanban task workflows`

### Task 12: Build human observability and fleet control surfaces

**Objective:** Let a human understand, watch, and intervene in agent sessions without knowing tmux/Kanban internals — and without needing to attach to every pane.

**Files:**
- Create: `src/hca/status.py`
- Create: `src/hca/observe.py`
- Create: `src/hca/events.py`
- Modify: `src/hca/cli.py`
- Create: `tests/unit/test_status.py`
- Create: `tests/unit/test_observe.py`
- Create: `tests/integration/test_live_observe.py`
- Create: `docs/operations.md`
- Create: `docs/observability.md`

**Commands:**

| Command | Purpose |
|---|---|
| `hca status [--watch] [--json]` | Logical task/run overview with current activity |
| `hca ps [--watch] [--json]` | Physical slot/process overview |
| `hca watch [--interval N]` | Mission-control auto-refresh table of all live sessions |
| `hca explain <task\|run>` | Why not running / blocked / waiting (exact reason) |
| `hca peek <task\|run\|slot> [--lines N]` | Read-only tmux pane snapshot (no attach) |
| `hca logs <task\|run> [--follow] [--since]` | Structured run log (pipeline + worker stderr) |
| `hca activity [--follow] [--task ...] [--json]` | Fleet or filtered lifecycle/tool event stream |
| `hca transcript <task\|run\|session_id> [--last N]` | Hermes conversation transcript for that session/run |
| `hca inspect <task>` | Deep one-page report: task + run + slot + session + workspace + last events + peek tail |
| `hca attach <task\|run\|slot>` | Explicit interactive tmux attach |
| `hca kill <task> --reason ...` | Operator stop |
| `hca retry <task>` | Operator retry |
| `hca dashboard` | Open Hermes Kanban dashboard for the fleet board |

**Composition model (source of truth per field):**

| Data | Primary source | Fallback |
|---|---|---|
| Task state / summary / block reason | Hermes Kanban | HCA state diagnostic |
| Run / PID / claim | Kanban run row | HCA mapping |
| Slot / tmux identity | HCA state | tmux list |
| Hermes session ID | HCA mapping + plugin bind | profile session search by time/task |
| Current activity | plugin activity events | peek tail / run log tail |
| Transcript | Hermes session store | run log reconstruction (best-effort) |
| Subagents | plugin leases/events | parent transcript tool results |
| Resources | HCA resource governor | backend metrics adapter |

Report source disagreement as a diagnostic field (`sources_disagree=true`), never silently pick a winner without labeling it.

**Live UX details:**
1. `hca watch` is the default human “is the fleet OK?” surface — color or plain text, `--json` for scripting, Ctrl-C clean exit.
2. Each live row shows a one-line **activity headline**, e.g. `tool:terminal pytest -q` or `model:thinking` or `blocked: human input` or `waiting: KV pressure 91%`.
3. `hca peek` uses tmux `capture-pane` only; never sends keys.
4. `hca transcript` resolves task → run → Hermes session ID, then renders user/assistant/tool turns with timestamps; supports `--last N` and `--json`.
5. `hca activity` streams append-only JSONL events: `run.start`, `run.heartbeat`, `tool.start`, `tool.end`, `subagent.start`, `subagent.stop`, `kanban.block`, `kanban.complete`, `run.fail`, `admission.wait`.
6. Optional notifications (local only unless a gateway deliver target is configured): task blocked, failed, completed, or stalled beyond heartbeat threshold.
7. Stale/stuck detection: no activity + no heartbeat beyond threshold surfaces `stale` in watch/status with age.
8. Multi-session log mux: `hca logs --all --follow` prefixes each line with `slot/task` for concurrent reading.
9. All observe commands support stable IDs (task, run, slot, hermes session) and `--json`.
10. Redaction pipeline applied to peek/logs/activity/transcript before display or file export.

**Do not build:** a second full web dashboard, a second chat UI, or a replacement for Hermes session browser. Prefer linking/opening Hermes dashboard + HCA terminal observability. If a terminal TUI is added later, it must call the same observe APIs as the CLI.

**Verification:**
```bash
pytest -q tests/unit/test_status.py tests/unit/test_observe.py tests/integration/test_live_observe.py
```
Expected: with a fake Hermes worker emitting activity events and a real tmux slot, `watch`/`peek`/`activity`/`transcript` resolve the correct run and never inject keystrokes.

**Commit:** `feat: add human session observability surfaces`

### Task 13: Replace backend verification and local-only checks

**Objective:** Validate actual compatibility and tool calling without brittle grep rules.

**Files:**
- Create: `src/hca/doctor.py`
- Create: `tests/integration/test_doctor.py`
- Deprecate: `scripts/check-backend.sh`, `scripts/verify-local-only.sh`

**Checks:**
- endpoint URL parsing; localhost/private-address requirement by default, explicit `--allow-remote` opt-out;
- `backend.engine` is `vllm` or `sglang` (or explicit secondary `openai_compat`);
- `/v1/models` exact served model ID for the chosen engine;
- chat completion and streaming if required;
- tool-calling round trip, not only text generation;
- engine-specific metrics endpoint reachable when configured (vLLM and SGLang adapters both probe);
- model context metadata where available;
- Hermes version/config migration;
- tmux and Git worktree support;
- plugin entry point enabled in all slot profiles;
- gateway dispatcher disabled for HCA board;
- profile descriptions/toolsets/config paths;
- dashboard binding/auth warning;
- disk/memory headroom and writable state/log/worktree roots;
- **UMA awareness:** report free/available memory; warn if pressure high (do not auto `drop_caches` unless configured);
- **cluster (SSH mode):** for each inventoried node — `ssh BatchMode` succeeds, **remote username matches control**, remote `hermes`/`tmux`/`hca` present, remote backend healthy on expected port (8000 vLLM / 30000 SGLang), remote capacity probe returns JSON; optional `ibdev2netdev` fabric check when `--fabric` set; fail with exact host + ssh error if passwordless SSH is broken.

**Verification:**
```bash
pytest -q tests/integration/test_doctor.py
hca doctor --json
hca doctor --engine vllm
hca doctor --engine sglang
hca cluster doctor
```

**Commit:** `feat: add end-to-end fleet doctor`

### Task 14: Redesign benchmarking around throughput, latency, memory, and orchestration

**Objective:** Find the measured concurrency knee for each machine/model/backend instead of publishing fixed worker counts.

**Files:**
- Create: `src/hca/benchmark.py`
- Create: `tests/unit/test_benchmark_analysis.py`
- Modify: `docs/benchmarking.md`
- Retain/deprecate: `scripts/benchmark.sh`
- Modify: `benchmarks/.gitkeep`

**Benchmark suites:**
1. Raw backend request sweep (**both** vLLM and SGLang when available on the host).
2. Full Hermes one-shot worker sweep.
3. tmux + Kanban supervisor sweep.
4. mixed parent workers + subagent fan-out.
5. long-context prefill stress.
6. heterogeneous task mix (research/code/review).
7. soak test with retries and compression.
8. (cluster) multi-node placement + node-loss reclaim.

**Artifacts:** immutable manifest, Hermes/backend **engine+version**, config hash, raw responses (redacted), per-run logs, TTFT, decode TPS, total TPS, success rate, p95 latency, token counts, CPU/system/GPU/unified memory, KV-cache metrics, power/thermal data when available, and chosen knee rationale. Publish separate knees for `engine=vllm` and `engine=sglang` on the same GB10 when both are measured.

**Autotune rule:** choose the highest concurrency before p95 latency/error/memory exceeds configured limits, not simply the maximum total TPS point. Save as machine-local recommendation under the engine name; never silently rewrite the repository's generic defaults.

**Verification:**
```bash
pytest -q tests/unit/test_benchmark_analysis.py
hca bench --dry-run --engine vllm --levels 1,2,3
hca bench --dry-run --engine sglang --levels 1,2,3
```

**Commit:** `perf: add adaptive concurrency benchmark`

### Task 15: Make monitoring portable and pressure-aware

**Objective:** Support Linux and macOS without GNU-only assumptions.

**Files:**
- Extend: `src/hca/telemetry.py`
- Create: `tests/unit/test_telemetry_linux.py`
- Create: `tests/unit/test_telemetry_macos.py`
- Deprecate: `scripts/health-monitor.sh`, `scripts/status.sh`

**Steps:**
1. Read system memory through portable Python/platform adapters.
2. Support NVIDIA `nvidia-smi`; make Apple metrics best-effort rather than failing.
3. Distinguish allocated GPU memory, system memory pressure, and backend KV-cache pressure.
4. Add hysteresis and rate-limited alerts.
5. Keep local logs and optional gateway notifications; never add outbound analytics by default.

**Verification:**
```bash
pytest -q tests/unit/test_telemetry_linux.py tests/unit/test_telemetry_macos.py
```

**Commit:** `fix: make fleet monitoring cross-platform`

### Task 16: Add deterministic failure and durability tests

**Objective:** Prove the core resilience claims with executable tests.

**Files:**
- Create: `tests/fixtures/fake_openai_server.py`
- Create: `tests/e2e/test_full_task_lifecycle.py`
- Create: `tests/e2e/test_fault_injection.py`
- Rewrite: `scripts/fault-injection-test.sh` as an E2E wrapper
- Modify: `docs/durability-tests.md`

**Fault matrix:**
- kill worker PID mid-tool;
- kill tmux server;
- kill/restart supervisor;
- backend unavailable at start;
- backend 429/500/invalid response;
- malformed or missing profile;
- stale claim/heartbeat;
- max runtime timeout;
- task exits without Kanban termination;
- duplicate supervisor start;
- profile-slot exhaustion;
- subagent lease exhaustion and orphan reclamation;
- worktree collision/dirty cleanup refusal;
- disk pressure and memory high watermark;
- manual block/comment/unblock;
- exact-session resume and fresh-session retry.

**Verification:**
```bash
pytest -q tests/e2e
```

**Commit:** `test: prove crash recovery and isolation invariants`

### Task 17: Rewrite public documentation and remove stale claims

**Objective:** Make the repository accurate, concise, and usable by a human without reading Hermes internals.

**Files:**
- Rewrite: `README.md`
- Rewrite: `SKILL.md`
- Rewrite: `docs/deployment-guide.md`
- Rewrite: `docs/workflow-patterns.md`
- Rewrite: `docs/tuning-guide.md`
- Rewrite: `docs/current-state-report.md`
- Create: `docs/architecture.md`
- Create: `docs/isolation-and-kv-cache.md`
- Create: `docs/subagent-policy.md`
- Create: `docs/observability.md`
- Create: `docs/operations.md`
- Create: `docs/migration-v1-to-v2.md`
- Modify: `references/research-report-summary.md`
- Remove or archive: self-assigned `100/100` grade docs after preserving useful evidence

**Required documentation corrections:**
- Explain that tmux is lifecycle/attachability, not GPU KV partitioning.
- Explain every actual isolation boundary.
- Remove `--continue` as blanket recovery advice.
- Replace NFS/shared-SQLite multi-node proposals with control-plane + **passwordless-SSH** node topology.
- Remove fixed universal concurrency and memory-budget claims from quick start; publish GB10-measured knees per engine.
- Separate measured artifacts from estimates.
- Document live delegation contract detection and docs/runtime mismatch.
- Show current Kanban tools rather than telling models to shell out to `hermes kanban`.
- Document dashboard, boards, profile descriptions/distributions, goal cards, task skills, worktrees, retries, diagnostics, structured handoffs, and notifications.
- **Ship GB10-first presets** (`gb10-vllm`, `gb10-sglang`, cluster variants) as the default path; keep node protocol portable for secondary hosts.
- Document vLLM and SGLang as equal first-class backends with shared doctor/bench/capacity surfaces; **cite NVIDIA playbook recipes** (ports, containers, agent-ready flags) instead of inventing Spark installs.
- Document cluster runbook: complete NVIDIA connect-* playbook first → passwordless SSH → `hca cluster nodes add` → doctor → up; no join tokens; no NFS SQLite.
- Document dual multi-node modes: per-node agent fleets (default) vs optional multi-node sharded vLLM/NCCL serve.
- Document UMA memory behavior and non-automatic cache-flush recovery.
- Document Hermes local security: localhost bind, no open Telegram without allowlists (when gateway used).
- Link [dgx-spark-playbooks](https://github.com/NVIDIA/dgx-spark-playbooks) and forum: https://forums.developer.nvidia.com/c/accelerated-computing/dgx-spark-gb10

**Verification:**
```bash
python -m pytest -q tests/contract tests/unit
python -m hca.cli docs-check
```

**Commit:** `docs: document resilient tmux Hermes fleets`

### Task 18: Add CI, migration, and release gates

**Objective:** Ship the rewrite safely and keep it compatible as Hermes evolves.

**Files:**
- Rewrite: `.github/workflows/ci.yml`
- Create: `.github/workflows/hardware-benchmark.yml`
- Create: `docs/migration-v1-to-v2.md`
- Modify: `CHANGELOG.md`, `CONTRIBUTING.md`

**CI matrix:**
- Linux GitHub runners only: unit tests, CLI smoke, tmux integration on portable paths. This is a **regression gate**, not a product platform claim.
- **Not** a macOS CI matrix — product target is DGX Spark / GB10 Linux; laptop macOS is optional local dev only.
- **GB10 / on-device validation** (required for release claims about engines, concurrency, cluster): `hca doctor`, `hca bench` (vLLM and SGLang separately), fleet smoke, passwordless-SSH cluster smoke. GitHub-hosted Ubuntu is not a substitute for Spark hardware.
- Hermes contract: minimum supported release and current upstream main (allowed-failure initially, required before stable v2).
- Security: secret scan, dependency audit, process-argument redaction test.
- Optional hardware workflow: GB10 self-hosted runner for real vLLM and/or SGLang knee measurements (manual or labeled jobs).

**Migration:**
1. Discover legacy role profiles and active tmux sessions.
2. Refuse migration while legacy workers are active unless `--drain-legacy` is chosen.
3. Back up profile configs and preserve memories/sessions.
4. Install slot profiles and HCA plugin.
5. Select preset: `gb10-vllm` / `gb10-sglang` / cluster variants / `generic-linux`.
6. Create/select a board per project.
7. Disable gateway dispatch only for the HCA operating mode and explain how to revert.
8. Validate with `hca doctor` and one fake/smoke task.
9. Keep rollback instructions and old wrappers for one release.

**Release gates:** unit/smoke green on Linux CI; docs accurate; real supervisor restart test passes on a Linux host; **at least one GB10-measured artifact for vLLM and one for SGLang when claiming dual-engine support** (or clearly mark “code-complete, measurement pending”); cluster smoke over **passwordless SSH** on real Sparks (or mocked SSH unit tests) before “cluster-ready” claims; Hermes `dispatch_once(spawn_fn=…)` contract verified. GitHub Ubuntu is never sufficient alone for engine/throughput claims.

**Commit:** `ci: gate HCA v2 across Linux and macOS`

---

## 6. Subagent Management Decision Table

| Work characteristic | Primitive | Reason |
|---|---|---|
| Short independent reasoning, result needed in parent context | `delegate_task(tasks=[...])` | Low coordination overhead, focused summaries |
| Mechanical 3+ tool-call loop | `execute_code` | No extra model loops |
| Long-running, retryable, human-visible, cross-role | Kanban child task in tmux | Durable and attachable |
| Code changes that may overlap | Separate Kanban worktree task | Prevent file collision |
| Needs user clarification/approval | Kanban block/comment/unblock | Subagents cannot clarify |
| Must survive parent `/new`, crash, or process exit | Kanban/tmux | Subagents are not durable |
| Multi-stage hierarchy | Kanban DAG | Durable dependencies and audit trail |
| Nested reasoning inside one bounded card | Optional orchestrator subagent, only with explicit policy | Avoid multiplicative fan-out by default |
| Recurring independent run | Hermes cron creates an idempotent Kanban task | Scheduling separated from execution |

### Recommended defaults

```yaml
delegation:
  max_concurrent_children: 2   # initial conservative value; HCA global gate is authoritative
  max_spawn_depth: 1
  orchestrator_enabled: false
  max_iterations: 30
  child_timeout_seconds: 0     # rely on progress/parent task runtime unless operator opts in
  subagent_auto_approve: false
```

These are starting points, not universal constants. `hca bench` may recommend a different fleet-wide budget, but the global HCA lease gate must remain authoritative across all worker processes.

---

## 7. Performance Optimization Opportunities

These serve the overarching goal: **maximize useful concurrent agent work on one large-memory host without losing isolation, human control, or resilience.**

### High priority (core scheduler)

1. **Shared backend, isolated clients:** one loaded model server, many separate Hermes processes.
2. **Adaptive admission:** derive active concurrency from measured pressure rather than fixed worker count.
3. **Weighted sequence credits:** long-context / llm-heavy / subagent-capable parents cost more than short reviews.
4. **Admit-before-claim + wave limits:** never claim work the backend cannot serve; large DAGs release in waves.
5. **Launch staggering:** avoid simultaneous long-prefill spikes that thrash KV and TTFT.
6. **Warm slots, cold models:** pre-create slots/profiles at `hca up`; do not keep idle model contexts warm unless explicitly configured.
7. **Context discipline:** fresh task sessions, concise role SOULs, project AGENTS.md, disk-first artifacts, bounded compression.
8. **Prefix stability for cache hits:** no mid-session toolset/system-prompt/model mutations; keep per-role prefixes byte-stable; load only task-needed skills.
9. **Minimal toolsets per role:** smaller schemas → cheaper prompts and better cache locality.
10. **Structured handoffs:** downstream agents consume Kanban summary/metadata instead of full logs/transcripts.
11. **Goal cards for completion:** explicit acceptance criteria and bounded judges for open-ended tasks.
12. **Fair scheduling:** priority + aging + per-role caps + retry/control reserve lane.
13. **Backend metrics first:** prefer actual KV/queue metrics over inferred GPU utilization.
14. **Auxiliary offload:** route compression/title/aux tasks to a separate small endpoint when available so they do not steal decode capacity from workers.
15. **One-shot worker shape by default:** `hermes chat -q "work kanban task …"` for task runs; avoid long interactive multi-task conversations in one process.
16. **Subagent thrift:** short leaf batches only; durable work becomes Kanban children (visible, retryable, capacity-accounted).
17. **Avoid model churn:** only dual-model if the second model is already resident/separate endpoint; never thrash one server across large model swaps mid-fleet.
18. **Disk/log retention:** prevent multi-hour fleets from dying on full disks; treat disk like memory.
19. **Capacity dry-run:** `hca plan --dry-run` estimates credits/waves before expensive fan-out.
20. **Root progress rollup:** keep the human on the critical path instead of micromanaging every child.

### Backend engines (first-class: vLLM and SGLang)

Both are **equal** OpenAI-compatible inference options on DGX Spark, matching NVIDIA playbooks:

| Concern | vLLM ([playbook](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/vllm)) | SGLang ([playbook](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/sglang)) |
|---|---|---|
| HCA surface | `backend.engine=vllm` | `backend.engine=sglang` |
| Typical port | **8000** | **30000** |
| Deploy style | Docker (`vllm/vllm-openai` / Spark recipes); ARM64 + CUDA 13 | Docker `lmsysorg/sglang:latest-cu130` |
| Health | `/v1/models` + metrics adapter | `/health`, `/v1/models` when OpenAI mode used |
| Capacity | normalized `capacity()` | normalized `capacity()` |
| Launch pack | `config/backends/vllm/` — **thin wrappers that cite/pin NVIDIA flags** | `config/backends/sglang/` — same |
| Presets | `gb10-vllm`, `gb10-cluster-vllm` | `gb10-sglang`, `gb10-cluster-sglang` |
| Bench | `hca bench --engine vllm` | `hca bench --engine sglang` |
| Multi-node model | Optional Ray/TP after connect-two-sparks + NCCL | Single-node primary in NVIDIA docs; treat multi-node as advanced if/when documented |

**vLLM Spark flags to treat as first-class recipe inputs (not hard-code forever — follow current playbook):**
- Continuous batching / OpenAI server
- `--enable-prefix-caching`, `--enable-chunked-prefill`, `--async-scheduling` where playbook uses them
- `--gpu-memory-utilization`, `--max-model-len`, `--max-num-seqs`, `--max-num-batched-tokens` as capacity knobs HCA can document and seed in presets
- Agent-ready paths: tool-call parsers / `--enable-auto-tool-choice` for Hermes tool use (see NVIDIA Hermes + Qwen3.6 NVFP4 recipe)
- MoE backends (e.g. marlin) only when the playbook’s model recipe requires them

**SGLang Spark flags:**
- Container GPU launch, port 30000
- NVFP4: `--quantization modelopt_fp4` when using NVFP4 checkpoints (per playbook)
- Prefer OpenAI-compatible client settings in HCA profiles when available; otherwise document generate API only as non-default

**UMA (all engines on Spark):**
- Unified memory can show OOM-like pressure even under “capacity.” HCA admission uses engine metrics + system memory; document NVIDIA’s manual cache flush as **operator recovery**, never auto-run mid-fleet without explicit config:
  `sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'`
- Prefer lowering `--max-num-seqs` / concurrency via `hca bench` over cache flushes.

**Security (from NVIDIA Hermes playbook):**
- Default bind inference to localhost / node-local; do not expose `:8000`/`:30000` on untrusted networks without auth.
- Cluster: workers on node A talk to node A’s engine; do not casually publish OpenAI ports across the LAN.

- Shared Hermes profile path: only `base_url` + served model id + engine adapter differ.
- Ollama/TRT-LLM/NIM remain secondary (`openai_compat` or future adapters), not equal to vLLM/SGLang in v2 claims.
- Cluster may mix engines across nodes; placement sees `engine` in probes.

### Backend-specific tuning knobs (adapter docs, not HCA core defaults)

- vLLM: continuous batching, prefix caching, chunked prefill, max sequences, batched-token limits, KV dtype, memory utilization, scheduler metrics.
- SGLang: continuous batching / radix or prefix cache settings as exposed by the build, max running requests, mem fraction, scheduler metrics.
- Do not copy vLLM flags into SGLang launch scripts or vice versa.
- Multiple endpoints: explicit role/backend routing only when models remain resident and the resource governor accounts for both.

No engine-specific flag belongs in HCA core defaults unless the generic OpenAI-compatible contract requires it.

### Large-task recipe (default human workflow)

```text
1. hca init / doctor / up          # warm slots, single dispatcher
2. hca task swarm|add --goal       # durable DAG / goal card
3. hca plan --dry-run              # credits + wave estimate
4. hca watch                       # observe sessions + root rollup
5. hca explain / comment / unblock # human gates only when needed
6. hca drain && hca down           # clean completion
```

Prefer: **Kanban DAG for the large task structure**, **tmux slots for execution isolation**, **delegate_task only for short in-card reasoning bursts**, **watch/peek/transcript for observation**.

---

## 8. Deliberate Non-Goals and Anti-Patterns

### Non-goals

- No shared SQLite Kanban over NFS or multiple hosts (cluster uses control-plane owner + SSH to nodes).
- No custom web dashboard; use and link Hermes' Kanban dashboard.
- No replacement of Hermes Kanban lifecycle, task DB, decomposer, or session store.
- No implicit cloud fallback in “local-only” mode.
- No per-call model roulette for subagents.
- No automatic nested delegation beyond depth 1.
- No automatic force-killing at a soft memory threshold.
- No mandatory herdr dependency. herdr may observe/attach/notify only; it must not claim tasks or own workers. tmux remains the v2 core invariant.
- No outbound telemetry or analytics.
- No self-awarded readiness score without executable evidence.
- No LLM in the control plane for admission, restart, or process ownership decisions.
- No ranking of vLLM above SGLang (or reverse) in product defaults — both are first-class; choice is operator/preset.
- No claim that macOS is a performance target; GB10 Linux is.
- **No mandatory cluster join-token / custom auth plane on GB10** — passwordless SSH is the default fabric; do not force operators to stand up a second credential system.

### Anti-patterns (reject in review)

1. `tmux send-keys` as a task API, or scraping pane text for completion.
2. Hermes gateway dispatcher and HCA dispatcher on the same board.
3. Treating tmux-session existence as worker health.
4. `hermes --continue` in a concurrent profile.
5. Multiple mutating agents in one checkout or shared `dir:` workspace.
6. Combining Kanban worktree allocation with `hermes -w`.
7. Claiming tasks before resource admission.
8. Static “spawn N” concurrency with no backend/KV feedback.
9. Assuming shared model weights means shared agent KV state.
10. Unbounded or nested delegation hidden from resource accounting.
11. Using subagents as durable background workers.
12. Treating SOUL.md/profiles as filesystem security boundaries.
13. Prose-only or scrollback-only artifact handoffs.
14. Automatic LLM decomposition as the default for expensive/destructive work.
15. Decorative dashboard/herdr/MCP/gateway integrations with no load-bearing role.
16. Shell façades without reconciliation, leases, or idempotency.
17. Making interactive `attach` the only way to see what agents are doing.
18. Dumping full unredacted transcripts or secrets into watch tables or shared logs.
19. Requiring a custom join-token HTTP mesh on GB10 clusters that already have passwordless SSH.
20. Password prompts or interactive SSH in automated dispatch paths.

### Completion handoff convention

Every task completes through `kanban_complete(summary=..., metadata=...)` with machine-readable metadata pointers (artifacts, commit, branch, verification commands, residual risk, `hermes_session_id`). Keep secrets, raw logs, and full transcripts out of Kanban; store content-addressed artifacts and pass pointers. Downstream agents consume summaries/manifests, not tmux output.

---

## 9. Acceptance Criteria

### Correctness and isolation

- [ ] Two code workers can run concurrently on the same repo without sharing a checkout, Hermes session, or `HERMES_HOME`.
- [ ] Every live Kanban run maps to exactly one tmux session and worker PID.
- [ ] No worker can read or mutate another board through inherited board state.
- [ ] `--continue` is absent from automated worker launch/recovery paths.
- [ ] Supervisor restart produces zero duplicate workers and adopts valid live runs.
- [ ] A worker crash is detected, recorded, retried, and circuit-broken according to Kanban policy.

### Subagents

- [ ] One global cap covers top-level workers plus transient subagent leases.
- [ ] Batch subagents are visible in `hca ps` with parent/child identity.
- [ ] Capacity denial degrades to Kanban or sequential work without runaway retries.
- [ ] Nested delegation is off by default and requires explicit config + test.
- [ ] Parent verifies every side-effectful subagent result.

### Human usability

- [ ] Fresh install reaches a working fleet through `hca init`, `hca doctor`, `hca up`.
- [ ] A user can add, inspect, attach, comment, retry, drain, and stop work without raw tmux commands.
- [ ] **A user can observe live agent sessions** via `status` / `watch` / `peek` / `logs` / `activity` / `transcript` without attaching.
- [ ] Activity headlines show the current tool or wait/block reason for each live run.
- [ ] Transcripts resolve by task → Hermes session ID with secret redaction.
- [ ] `hca explain` states the exact admission/block/stuck reason.
- [ ] `hca doctor` explains every configuration conflict and exact fix.
- [ ] Hermes dashboard remains available as the visual board.
- [ ] Observe paths are non-intrusive by default; attach is explicit.

### Performance and resilience

- [ ] `hca bench` finds a machine-specific concurrency knee with reproducible artifacts.
- [ ] High memory/KV pressure stops new admission and resumes with hysteresis.
- [ ] Linux and macOS tests pass.
- [ ] 24-hour soak test completes without orphaned leases, duplicate sessions, unbounded logs, or unreconciled runs.

---

## 10. Authoritative References

### NVIDIA DGX Spark (primary ops)

- Playbooks index: https://github.com/NVIDIA/dgx-spark-playbooks
- Connect two Sparks (QSFP + passwordless SSH): https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-two-sparks
- Connect three Sparks (ring): https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-three-sparks
- Multi Sparks through switch: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/multi-sparks-through-switch
- vLLM for Inference: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/vllm
- SGLang for Inference: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/sglang
- Hermes Agent with local models: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/hermes-agent
- Connect to your Spark (SSH/mDNS/NVIDIA Sync): https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-to-your-spark
- NCCL for two Sparks: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/nccl
- Tailscale: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/tailscale
- Developer forum: https://forums.developer.nvidia.com/c/accelerated-computing/dgx-spark-gb10

### Hermes Agent

- Hermes architecture: https://hermes-agent.nousresearch.com/docs/developer-guide/architecture
- Agent loop: https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop
- Programmatic integration / TUI RPC: https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration
- Tools runtime: https://hermes-agent.nousresearch.com/docs/developer-guide/tools-runtime
- Context compression/caching: https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching
- Delegation: https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation
- Delegation patterns: https://hermes-agent.nousresearch.com/docs/guides/delegation-patterns
- Kanban: https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban
- Kanban worker lanes: https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-worker-lanes
- Goals: https://hermes-agent.nousresearch.com/docs/user-guide/features/goals
- Profiles: https://hermes-agent.nousresearch.com/docs/user-guide/profiles
- Profile distributions: https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions
- Sessions: https://hermes-agent.nousresearch.com/docs/user-guide/sessions
- Worktrees: https://hermes-agent.nousresearch.com/docs/user-guide/git-worktrees
- Checkpoints: https://hermes-agent.nousresearch.com/docs/user-guide/checkpoints-and-rollback
- Hooks: https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks
- Plugins: https://hermes-agent.nousresearch.com/docs/developer-guide/plugins
- Context files: https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files
- Fallback providers: https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers
- Managed scope: https://hermes-agent.nousresearch.com/docs/user-guide/managed-scope
- Security: https://hermes-agent.nousresearch.com/docs/user-guide/security

---

## 11. Execution Order

Implement in this dependency order:

1. Tasks 1-3: compatibility contract, CLI, state (+ presets scaffolding).
2. Tasks 4-6: isolated profiles, tmux, Kanban adapter (**single-node GB10 path complete**).
3. Tasks 7-10: supervisor, **vLLM+SGLang resource governor**, subagent plugin, workspaces.
4. Tasks 11-13: Kanban UX, human observability, doctor (engine-aware).
5. Tasks 14-16: dual-engine bench, portable telemetry, E2E fault testing.
6. **Cluster tasks (fold into Tasks 7/12/18 or add Task 19):** SSH transport, node inventory, remote probe/up, placement, `watch --cluster`, remote peek/attach/logs, node-loss reclaim.
7. Tasks 17-18: docs (`gb10.md`, `gb10-cluster.md`, `backends-vllm-sglang.md`), migration, CI, release gates.

Ship order for users:
1. **Single GB10 + vLLM or SGLang** (must work excellently first).
2. **GB10 cluster** (same node supervisor × N + control plane).
3. Secondary Linux hosts / macOS CLI compat.

Do not begin documentation claims about “resilient,” “crash-recovering,” “macOS-compatible,” or measured throughput until the corresponding executable acceptance tests pass.

---

## 12. Holistic Review Notes (2026-07-12)

Independent design synthesis and live Hermes source/docs audit both converge on the same core:

> HCA is a deterministic tmux executor and resource-aware reconciler around Hermes’ durable Kanban kernel — not another agent framework and not a shell wrapper.

### Resolved design tensions

| Tension | Decision |
|---|---|
| Per-run tmux session vs durable slot | **Durable slots** + one active run per slot; fresh Hermes process/session per run |
| Docs say sync `delegate_task`; runtime may show background delivery | **Capability-probe** the live schema; default policy treats subagents as non-durable parent-turn bursts |
| Private `dispatch_once(spawn_fn=...)` vs public CLI only | Use versioned private adapter + contract tests; fail closed if signature drifts; document upstream launcher hook as opportunity |
| Static worker count vs adaptive | **Adaptive weighted credits** with measured knee; static N only as seed/fallback |
| herdr | Optional observer only; never core |

### Gaps closed in final review (goal-aligned)

- **Primary target = DGX Spark / GB10 single + Spark cluster**; other devices secondary; macOS tertiary/compat.
- **Aligned with NVIDIA dgx-spark-playbooks:** QSFP topologies, same username, discover-sparks passwordless SSH, vLLM/SGLang Docker recipes, Hermes local-model path, UMA notes, Tailscale as operator access only.
- Cluster topology: control-plane Kanban owner + per-node supervisors over **passwordless SSH** — **not** NFS SQLite, **not** join tokens as primary.
- Separated **agent fleet multi-node** (default) from **sharded multi-node serve** (optional NVIDIA vLLM/NCCL path).
- Colocate agent runs with local inference by default; cross-node OpenAI ports discouraged without auth.
- **vLLM and SGLang equal first-class** (ports 8000 / 30000; peer presets; playbook-cited launch packs).
- Warm slots, wave admission, disk retention, unattended approvals, root rollup, `plan --dry-run` retained.
- Human observability non-intrusive; remote observe uses SSH.
- Ship order: single-node excellence → cluster → secondary hosts.

### Residual risks to track during implementation

1. Hermes may evolve `dispatch_once` / worker env contracts — contract tests must fail loudly.
2. Public docs vs runtime for `delegate_task` may stay mismatched — doctor reports active contract.
3. Engine metrics routes differ by vLLM/SGLang build — adapters must tolerate missing fields.
4. NVIDIA playbook container tags/flags change — pin versions in HCA packs and re-validate against current playbooks.
5. SSH flakiness / host key changes / non-BatchMode configs — `cluster doctor` must surface exact `ssh` errors; use multiplexing carefully.
6. Untracked stale `INTEGRATION_PLAN.md` must not be committed as current truth.
7. Session activity hooks may need peek/log fallback (`activity_source=fallback`).
8. Wave scheduling must not starve low-priority roles — fairness + aging tests.
9. Warm slots must not leave idle Hermes processes holding KV/context.
10. Mixing vLLM and SGLang nodes needs clear affinity + capacity reporting.
11. High-frequency SSH probes without ControlMaster can add latency — multiplex or cache capacity.
12. UMA pressure may require operator intervention beyond pure sequence credits — document, measure, don’t silent-fail.
13. Multi-node Ray/TP setups are fragile if QSFP/NCCL misconfigured — keep out of HCA default path.
