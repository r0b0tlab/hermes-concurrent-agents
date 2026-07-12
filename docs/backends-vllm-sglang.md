# vLLM and SGLang — equal first-class backends

Both engines share the HCA surface: `backend.engine`, OpenAI-compatible endpoint, doctor probes, normalized `capacity()`, presets, and benches.

| | vLLM | SGLang |
|---|---|---|
| Preset | `gb10-vllm` | `gb10-sglang` |
| Typical URL | `http://127.0.0.1:8000/v1` | `http://127.0.0.1:30000/v1` |
| NVIDIA playbook | [vllm](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/vllm) | [sglang](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/sglang) |
| Adapter | `hca.backends.vllm` | `hca.backends.sglang` |

```bash
hca init --preset gb10-vllm --model <served-model-id>
# or
hca init --preset gb10-sglang --model <served-model-id>
hca doctor
```

Launch packs under `config/vllm/` and `config/sglang/` thin-wrap NVIDIA recipes (do not invent Spark-only forks of flags). Prefer playbook-current containers and options.

## Hermes tool-calling requirements

Hermes agents need working tool calls from the endpoint
([providers doc](https://hermes-agent.nousresearch.com/docs/integrations/providers)):

- **vLLM:** launch with `--enable-auto-tool-choice --tool-call-parser hermes`
- **SGLang:** launch with `--tool-call-parser qwen` (or the parser matching your model)
- Both: serve **≥64k context** (`--max-model-len` / `--context-length`) — Hermes requires it for agent use.

The shipped launch packs set these by default. Verify with `hca doctor --tools`.

## Metrics

- vLLM exposes Prometheus metrics at `:8000/metrics` out of the box.
- SGLang needs `--enable-metrics` (set in the shipped launch pack) for `:30000/metrics`;
  without it HCA falls back to `/health`-only capacity checks.

## UMA note (DGX Spark)

If memory pressure appears within capacity, NVIDIA documents:

```bash
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
```

HCA must not run this automatically mid-fleet unless explicitly configured. Prefer lowering concurrency via admission / `hca bench`.

## Security

Bind engines to localhost on single-node fleets. On clusters, keep OpenAI ports node-local; Hermes workers colocate with the engine on the same Spark.
