# hermes-concurrent-agents

> **By [@mr-r0b0t on X](https://x.com/mr_r0b0t) — [r0b0tlab](https://github.com/r0b0tlab)**

**GB10 / DGX Spark first.** Concurrent [Hermes Agent](https://github.com/NousResearch/hermes-agent) fleets on local **vLLM** or **SGLang**, with durable **tmux** isolation, **Kanban** task truth, adaptive admission, and human observability (`hca watch` / `peek` / `activity`).

Full plan: [docs/plans/2026-07-12-hermes-agent-modernization.md](docs/plans/2026-07-12-hermes-agent-modernization.md)  
NVIDIA ops: [docs/nvidia-playbooks.md](docs/nvidia-playbooks.md) · [dgx-spark-playbooks](https://github.com/NVIDIA/dgx-spark-playbooks)

## Why

Agent work is parallel. One loaded model server can continuously batch many isolated Hermes processes. HCA keeps **agent state separated** (tmux + profile slots + sessions + worktrees) while **sharing** the inference backend — and admits work before it overruns unified memory / KV.

## Quick start (single DGX Spark)

```bash
# 0) Serve a model (NVIDIA playbooks — do not invent flags)
#    vLLM:  https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/vllm   (:8000)
#    SGLang: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/sglang (:30000)

git clone https://github.com/r0b0tlab/hermes-concurrent-agents.git
cd hermes-concurrent-agents
pip install -e ".[dev]"

# vLLM
hca init --preset gb10-vllm --model <served-model-id>
# or SGLang
# hca init --preset gb10-sglang --model <served-model-id>

hca doctor
hca up
hca watch
```

Legacy `setup.sh` / `scripts/spawn.sh` still exist as deprecation wrappers → prefer `hca`.

## Cluster (after NVIDIA connect playbooks)

```bash
# Prerequisites: connect-two-sparks | connect-three-sparks | multi-sparks-through-switch
# same username, QSFP fabric, passwordless SSH (discover-sparks)

hca init --preset gb10-cluster-vllm --model <served>
hca cluster nodes add spark-a spark-b   # prefer QSFP/CX7 IPs
hca cluster doctor
hca up --role control
hca cluster nodes up
hca watch
```

## Commands

| Command | Purpose |
|---|---|
| `hca init --preset …` | State dir + slot profiles |
| `hca doctor` | Hermes / tmux / vLLM|SGLang / SSH checks |
| `hca up` | Warm durable tmux slots + reconcile |
| `hca watch` / `ps` | Live mission control |
| `hca peek <slot>` | Read-only pane capture (no attach) |
| `hca activity --follow` | Event stream |
| `hca explain <id>` | Admission / wait reason |
| `hca cluster nodes add|up` / `doctor` | Passwordless-SSH fabric |

## Architecture (short)

- **Kanban** = durable task truth (Hermes)
- **HCA supervisor** = deterministic admission + tmux spawn ownership
- **Slots** = `hca-<fleet>-<role>-NN` tmux sessions (no `:` in names)
- **Engines** = vLLM and SGLang equal first-class
- **Cluster** = control node owns Kanban; nodes reached via **passwordless SSH** (not NFS SQLite, not join tokens)

## Status

v2.0.0a1 — foundational control plane shipped (init/doctor/up/watch/peek/activity/cluster/presets/tests).  
Still landing from the plan: full Kanban `spawn_fn` dispatch loop, deep transcript, full bench harness, plugin packaging polish, docs rewrite of legacy SKILL claims.

## License

MIT — see LICENSE
