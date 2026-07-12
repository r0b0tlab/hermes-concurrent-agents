# GB10 / DGX Spark cluster

## Prerequisites (NVIDIA — not HCA)

Complete the matching playbook first:

- 2 nodes: [connect-two-sparks](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-two-sparks) + `discover-sparks`
- 3 nodes: [connect-three-sparks](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-three-sparks)
- N nodes: [multi-sparks-through-switch](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/multi-sparks-through-switch)

Requirements:

- Same username on all Sparks
- QSFP / CX7 fabric up (`ibdev2netdev`)
- Passwordless SSH both ways

See also [nvidia-playbooks.md](nvidia-playbooks.md).

## HCA setup

```bash
hca init --preset gb10-cluster-vllm --model <served>
# or gb10-cluster-sglang

hca cluster nodes add spark-a spark-b   # prefer QSFP IPs
hca cluster doctor
hca up --role control
hca cluster nodes up
hca watch
```

## Modes

1. **Agent fleet (default)** — each node runs its own engine + Hermes workers; SSH is control only.
2. **Sharded serve (optional)** — multi-node vLLM/NCCL for one large model; point HCA at that endpoint; do not treat as default fleet topology.

## Rules

- No NFS SQLite for Kanban
- No join tokens as primary auth
- Inference HTTP stays node-local; SSH is orchestration
- Node loss → mark unhealthy → reclaim/requeue
