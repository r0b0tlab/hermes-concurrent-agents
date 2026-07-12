# Changelog

## 2.0.0a1 - 2026-07-12

### Added
- `hca` Python control plane (`pip install -e .`) — init, doctor, up, ps/watch, peek, activity, explain, cluster SSH inventory
- GB10 presets: `gb10-vllm`, `gb10-sglang`, `gb10-cluster-vllm`, `gb10-cluster-sglang`, `generic-linux`
- Equal first-class vLLM + SGLang adapters and capacity admission
- Durable tmux slot manager (no colon session names; warm idle slots)
- SQLite reconciliation state DB + leader lock
- Hermes `dispatch_once(spawn_fn=…)` contract tests
- NVIDIA playbook alignment docs (`docs/nvidia-playbooks.md`, backends guide)
- Modernization plan: `docs/plans/2026-07-12-hermes-agent-modernization.md`

### Changed
- README reoriented to DGX Spark / GB10 first; legacy shell scripts deprecated as wrappers
- SGLang is first-class (no longer “experimental” in product posture)

### Deprecated
- Direct use of `scripts/spawn.sh`, `status.sh`, `shutdown.sh` for fleet ops (use `hca`)

## 1.0.1 - 2026-05-12
### Added / Changed
- Prior shell-based concurrent agent scaffolding (see git history)
