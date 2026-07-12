# hermes-concurrent-agents

> **By [@mr-r0b0t on X](https://x.com/mr_r0b0t) — [r0b0tlab](https://github.com/r0b0tlab)**

**GB10 / DGX Spark first.** Concurrent [Hermes Agent](https://github.com/NousResearch/hermes-agent) fleets on local **vLLM** or **SGLang**, with durable **tmux** isolation, **Kanban** task truth, adaptive admission, and human observability.

## Install

```bash
git clone https://github.com/r0b0tlab/hermes-concurrent-agents.git
cd hermes-concurrent-agents
pip install -e ".[dev]"
# Hermes Agent + tmux required on PATH
```

## Single Spark (vLLM)

```bash
# Engine: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/vllm
hca init --preset gb10-vllm --model <served-model-id>
hca doctor
hca up --daemon
hca watch
hca task add "Implement feature X" --role coder
```

## Single Spark (SGLang)

```bash
# Engine: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/sglang  (:30000)
hca init --preset gb10-sglang --model <served-model-id>
hca doctor && hca up
```

## Cluster (after NVIDIA connect-* + passwordless SSH)

```bash
hca init --preset gb10-cluster-vllm --model <id>
hca cluster nodes add spark-a spark-b
hca cluster doctor && hca cluster nodes up
hca up --role control
hca watch
```

## Commands

| Command | Purpose |
|---|---|
| `hca init --preset …` | State + slot profiles |
| `hca doctor` | Hermes / tmux / engine / SSH |
| `hca up [--daemon]` | Warm slots, reconcile, Kanban dispatch |
| `hca drain` / `down` | Stop admits / shut down |
| `hca watch` / `ps` | Live mission control |
| `hca peek` / `logs` / `activity` / `transcript` | Observe without attach |
| `hca attach` | Interactive (opt-in) |
| `hca plan` / `bench` | Capacity estimate / measure knee |
| `hca task …` | Kanban helpers |
| `hca cluster …` | SSH inventory / doctor / nodes up |

## Docs

- [Architecture](docs/architecture.md)
- [Operations](docs/operations.md)
- [Observability](docs/observability.md)
- [GB10 cluster](docs/gb10-cluster.md)
- [vLLM & SGLang](docs/backends-vllm-sglang.md)
- [NVIDIA playbooks](docs/nvidia-playbooks.md)
- [Subagent policy](docs/subagent-policy.md)
- [Isolation / KV](docs/isolation-and-kv-cache.md)
- [Benchmarking](docs/benchmarking.md)
- [Plan](docs/plans/2026-07-12-hermes-agent-modernization.md)

## Architecture (one line)

Kanban = task truth · HCA = admission + tmux spawn · vLLM/SGLang = shared inference · SSH = Spark fabric · watch/peek = human eyes.

## License

MIT
