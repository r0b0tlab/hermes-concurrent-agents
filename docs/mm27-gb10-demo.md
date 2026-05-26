# MiniMax M2.7 NVFP4 GB10 Local Agent Team Demo

This guide prepares `hermes-concurrent-agents` for a fully local Hermes Agent team powered by MiniMax M2.7 NVFP4 on a local OpenAI-compatible vLLM endpoint.

## Key Runtime Contract

- Model: MiniMax M2.7 NVFP4.
- Backend family: vLLM OpenAI-compatible server.
- Optimized kernel path: FlashInfer-CUTLASS.
- Dual-GB10 tensor parallel mode is the target for the public demo.
- Hermes profiles must point to the local endpoint only.
- The served model name in Hermes profiles must exactly match vLLM `--served-model-name` and `/v1/models`.

## Recommended Environment

```bash
export HCA_ENDPOINT=http://127.0.0.1:8000/v1
export HCA_MODEL_NAME=minimax-m27-nvfp4
export HCA_PROVIDER_NAME=local-mm27-vllm
```

Use the same values for setup, backend checks, benchmarks, and demo scripts.

## Launch Notes

Use the already validated MiniMax M2.7 NVFP4 launch assets on this machine. For recording with OBS, keep additional memory headroom and use the reduced context profile when needed:

```bash
export MAX_MODEL_LEN=147456
export VLLM_NVFP4_GEMM_BACKEND=flashinfer-cutlass
```

The vLLM launch must include the matching served name, for example:

```bash
--served-model-name "$HCA_MODEL_NAME"
```

If a fresh server fails on its first request, stop the server, clean leaked shared-memory files, and relaunch before recording:

```bash
rm -f /dev/shm/*vllm* /dev/shm/*ray*
```

## Verify Backend

```bash
bash scripts/check-backend.sh \
  --endpoint "$HCA_ENDPOINT" \
  --model "$HCA_MODEL_NAME"
```

The check calls `/v1/models` and then sends a small chat completion.

## Create Demo Profiles

```bash
bash scripts/setup-mm27-demo.sh \
  --endpoint "$HCA_ENDPOINT" \
  --model "$HCA_MODEL_NAME" \
  --provider "$HCA_PROVIDER_NAME" \
  --force
```

Created profiles:

- `mm27-orchestrator`
- `mm27-coder`
- `mm27-research`
- `mm27-creative`
- `mm27-qa`

Verify the public "fully local" claim before recording:

```bash
bash scripts/verify-local-only.sh \
  --profiles mm27-orchestrator,mm27-coder,mm27-research,mm27-creative,mm27-qa \
  --endpoint "$HCA_ENDPOINT" \
  --provider "$HCA_PROVIDER_NAME" \
  --model "$HCA_MODEL_NAME"
```

Add `--smoke` after the backend is warm to make each profile answer through the local endpoint.

## OBS-Friendly Team Layout

```bash
DEMO_WS=/home/r0b0tdgx/demo-runs/mm27-local-agent-team/$(date -u +%Y%m%dT%H%M%SZ)

bash scripts/spawn-mm27-demo.sh \
  --session mm27-demo \
  --workspace "$DEMO_WS" \
  --prefix mm27

tmux attach -t mm27-demo
```

The tmux layout contains five panes: orchestrator, coder, research, creative, and QA.

## Suggested Mission

```text
You are the orchestrator for a fully local MiniMax M2.7 NVFP4 Hermes Agent team.
Coordinate coder, research, creative, and QA workers through the kanban board.
Complete a small local-agent-demo-dashboard project in the provided workspace.
Do not use external APIs. Require QA PASS before final report. Save every artifact to disk.
```

## Benchmark Evidence

Use the same model and endpoint values:

```bash
bash scripts/benchmark.sh \
  --levels 1,2,3,4 \
  --endpoint "$HCA_ENDPOINT" \
  --model "$HCA_MODEL_NAME" \
  --output-dir benchmarks/mm27
```

Only publish throughput claims that cite the resulting artifact directory.
