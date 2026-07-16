# Existing vLLM and SGLang endpoints

HCA does **not** install, launch, stop, replace, or own a model server. Configure
vLLM, SGLang, a hosted provider, or another OpenAI-compatible endpoint through
Hermes and operator-owned infrastructure first. NVIDIA's current DGX Spark
playbooks are the upstream reference for Spark serving containers and flags.

HCA's optional backend adapters consume health and admission signals only:

| Adapter | Health probes | Optional telemetry |
|---|---|---|
| vLLM | `/v1/models` | Prometheus request, cache, and scheduler metrics |
| SGLang | `/health`, `/v1/models` | Prometheus running/queued/token metrics |
| Generic OpenAI-compatible | `/v1/models`, bounded chat/tool probe | No fabricated capacity signal |

Missing or schema-drifted telemetry becomes unknown/degraded rather than zero.

## Select an existing endpoint

```bash
hca init --preset gb10-vllm --model <served-model-id> --source-profile default
# or
hca init --preset gb10-sglang --model <served-model-id> --source-profile default
hca doctor --tools
```

The source Hermes profile remains provider/config/credential authority. HCA does
not copy its connection string into fleet state. Package-preset localhost URLs
can be reconstructed from package data; custom URLs must be supplied at runtime
with `--config`, `--endpoint`, or `HCA_BACKEND_ENDPOINT`.

## Tool and context requirements

Use the model/runtime parser and chat template documented for the exact model.
Validate tool calls with `hca doctor --tools`; do not infer support from an image
name or launch flag alone.

There is no universal HCA context-length minimum. Choose a context window from
the model's supported limit and the measured workload. Longer context consumes
more KV capacity and can lower safe concurrency.

## Admission behavior

- Adapter telemetry is advisory input to HCA's sequence-credit admission.
- When telemetry is unavailable, HCA uses the conservative configured limit.
- GB10 defaults are starting points, not universal performance claims.
- HCA never restarts an endpoint or flushes host caches as recovery.
- `endpoint_changed` or stale telemetry causes a conservative hold/fallback,
  not guessed capacity.

## Multi-node inference

A Hermes profile may address an operator-owned endpoint on another host or an
operator-managed sharded serving deployment. HCA's controller, workers, Kanban
board, state, and workspaces still remain on one host. This is remote inference,
not remote agent placement.

## Upstream references

- [Hermes provider configuration](https://hermes-agent.nousresearch.com/docs/integrations/providers)
- [NVIDIA DGX Spark playbooks](https://github.com/NVIDIA/dgx-spark-playbooks)
- [vLLM documentation](https://docs.vllm.ai/)
- [SGLang documentation](https://docs.sglang.ai/)

These projects retain their own licenses and trademarks. HCA is not affiliated
with or endorsed by their maintainers.
