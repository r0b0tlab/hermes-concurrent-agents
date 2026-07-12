# Deployment guide (v2)

## Targets

| Priority | Platform |
|---|---|
| P0 | Single DGX Spark / GB10 |
| P0 | Spark cluster (QSFP + passwordless SSH) |
| P1 | Other Linux high-memory hosts |
| P2 | macOS (CLI/dev only) |

## Install

```bash
git clone https://github.com/r0b0tlab/hermes-concurrent-agents.git
cd hermes-concurrent-agents
pip install -e ".[dev]"
# Hermes Agent must be installed and on PATH
```

## Backends

Equal first-class:

- **vLLM** — typically `http://127.0.0.1:8000/v1` — [playbook](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/vllm)
- **SGLang** — typically `http://127.0.0.1:30000/v1` — [playbook](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/sglang)

Presets: `gb10-vllm`, `gb10-sglang`, `gb10-cluster-vllm`, `gb10-cluster-sglang`, `generic-linux`.

## Profiles / slots

`hca init` creates per-slot Hermes profiles under `~/.hermes/profiles/hca-<fleet>-<role>-NN` with local OpenAI-compatible provider config.

## State

`~/.hca/<fleet>/` (override with `--state-dir`):

- `hca.sqlite` — control mappings (not Kanban truth)
- `fleet.resolved.json`
- `logs/`
- `worktrees/`
- `nodes.json` (cluster)
- `DRAIN` flag

## Security

- Default local-only endpoints
- Do not expose OpenAI ports on untrusted networks
- Redaction on peek/transcript
- Approvals: `--yolo` only when intentionally unattended

## Legacy shell scripts

`setup.sh` and `scripts/*.sh` remain for compatibility; prefer `hca`.
