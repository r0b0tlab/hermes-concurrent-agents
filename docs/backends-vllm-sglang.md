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

Launch packs under `config/backends/` should thin-wrap NVIDIA recipes (do not invent Spark-only forks of flags). Prefer playbook-current containers and options.

## UMA note (DGX Spark)

If memory pressure appears within capacity, NVIDIA documents:

```bash
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
```

HCA must not run this automatically mid-fleet unless explicitly configured. Prefer lowering concurrency via admission / `hca bench`.

## Security

Bind engines to localhost on single-node fleets. On clusters, keep OpenAI ports node-local; Hermes workers colocate with the engine on the same Spark.
