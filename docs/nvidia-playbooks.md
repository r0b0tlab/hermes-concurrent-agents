# NVIDIA DGX Spark playbooks — HCA index

HCA composes with NVIDIA playbooks; it does not reimplement Spark networking or engine installs.

| Topic | Playbook |
|---|---|
| Index | https://github.com/NVIDIA/dgx-spark-playbooks |
| Laptop SSH / mDNS / NVIDIA Sync | https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-to-your-spark |
| 2-node QSFP + passwordless SSH | https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-two-sparks |
| 3-node ring | https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-three-sparks |
| N-node switch | https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/multi-sparks-through-switch |
| NCCL | https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/nccl |
| vLLM | https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/vllm |
| SGLang | https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/sglang |
| Hermes + local vLLM | https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/hermes-agent |
| Tailscale | https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/tailscale |
| Forum | https://forums.developer.nvidia.com/c/accelerated-computing/dgx-spark-gb10 |

## HCA sequence on Spark

1. Complete relevant NVIDIA connect-* playbook (same username, QSFP, `discover-sparks`).
2. Start vLLM or SGLang per NVIDIA recipe (ports 8000 / 30000).
3. `pip install -e .` in this repo; `hca init --preset gb10-vllm --model <served>`.
4. `hca doctor && hca up && hca watch`.
5. Cluster: `hca cluster nodes add … && hca cluster doctor && hca cluster nodes up`.

## Multi-node modes

- **Agent fleet (default):** per-node engine + Hermes workers; SSH control plane.
- **Sharded serve (optional):** multi-node vLLM/NCCL for one large model; configure HCA backend endpoint only.
