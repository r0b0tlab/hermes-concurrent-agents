# NVIDIA DGX Spark playbooks — HCA reference

HCA may consume infrastructure prepared with NVIDIA playbooks; it does not
reimplement Spark networking, install drivers, launch serving containers, or
own a distributed agent fabric.

| Topic | Upstream playbook |
|---|---|
| Index | <https://github.com/NVIDIA/dgx-spark-playbooks> |
| Connect to one Spark | <https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-to-your-spark> |
| Two/three/switch networking | <https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia> |
| NCCL | <https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/nccl> |
| vLLM | <https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/vllm> |
| SGLang | <https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/sglang> |
| Hermes Agent | <https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/hermes-agent> |

## Supported single-host sequence

1. The operator prepares one GB10 and an existing model endpoint independently
   of HCA.
2. Configure and authenticate a Hermes source profile for that endpoint.
3. Install HCA and initialize a single-host preset:

   ```bash
   hca init --preset gb10-vllm --model <served-model-id> --source-profile default
   # or: --preset gb10-sglang
   hca doctor
   ```

4. Start work through `hca run`, then inspect and collect the result.

HCA never starts, stops, replaces, or reconfigures a protected serving workload
as part of ordinary orchestration.

## Multi-node networking boundary

NVIDIA connect/NCCL playbooks can support an inference endpoint spanning or
hosted on other nodes. HCA may use that endpoint through a Hermes profile while
its controller, Kanban board, workers, state, and workspaces remain on one host.

Completing a networking playbook does not enable HCA remote agent placement.
Read-only SSH reachability is not placement/recovery evidence. See
[Remote placement and GB10 clusters](gb10-cluster.md).
