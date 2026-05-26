# Deployment Guide

Step-by-step guide to deploying concurrent Hermes agents on a local OpenAI-compatible model backend.

The repo is model-agnostic: you choose the model, endpoint, and runtime flags. The only hard requirement is that your backend exposes `/v1/models` and `/v1/chat/completions` and that every Hermes profile uses the exact served model name.

## Phase 1: Choose a Model and Endpoint

Set these values once and reuse them for setup, checks, benchmarks, and demos:

```bash
export HCA_ENDPOINT=http://127.0.0.1:8000/v1
export HCA_MODEL_NAME=your-served-model-name
export HCA_PROVIDER_NAME=local-vllm
```

`HCA_MODEL_NAME` must match the model id returned by:

```bash
curl "$HCA_ENDPOINT/models"
```

## Phase 2: Start an Inference Backend

### Option A: vLLM

Generic vLLM launch shape:

```bash
export HCA_MODEL_PATH=/path/or/hf-id/of/your/model
export HCA_MODEL_NAME=your-served-model-name

python3 -m vllm.entrypoints.openai.api_server \
  --model "$HCA_MODEL_PATH" \
  --served-model-name "$HCA_MODEL_NAME" \
  --host 0.0.0.0 --port 8000 \
  --trust-remote-code \
  --gpu-memory-utilization 0.70 \
  --max-model-len 65536 \
  --max-num-seqs 16
```

Add only the backend-specific flags required by your chosen model/runtime. Do not copy kernel flags from another model family without verifying server logs.

Docker Compose path:

```bash
export HCA_MODEL_PATH=/path/or/hf-id/of/your/model
export HCA_MODEL_NAME=your-served-model-name
docker compose -f config/vllm/docker-compose.yml up -d
```

### Option B: MiniMax M2.7 NVFP4 demo path

For the local MiniMax M2.7 NVFP4 agent-team demo, use the dedicated guide:

```bash
less docs/mm27-gb10-demo.md
```

That path targets the optimized FlashInfer-CUTLASS runtime and keeps separate setup scripts/profiles so the public demo is not confused with other model recipes.

### Option C: Ollama / llama.cpp / other OpenAI-compatible servers

Any server is usable if it exposes OpenAI-compatible chat completions:

```bash
export HCA_ENDPOINT=http://127.0.0.1:11434/v1
export HCA_MODEL_NAME=llama3.1:8b
```

## Phase 3: Verify Backend

```bash
bash scripts/check-backend.sh \
  --endpoint "$HCA_ENDPOINT" \
  --model "$HCA_MODEL_NAME"
```

This validates `/v1/models` and one small chat completion.

## Phase 4: Profile Setup

```bash
cd hermes-concurrent-agents
bash setup.sh \
  --model "$HCA_MODEL_NAME" \
  --endpoint "$HCA_ENDPOINT" \
  --provider "$HCA_PROVIDER_NAME" \
  --force
```

This creates/updates five isolated profiles:

- `creative-worker`
- `coder-worker`
- `research-worker`
- `qa-worker`
- `orchestrator`

Existing profile configs are preserved unless `--force` is passed; forced replacement creates timestamped backups.

Verify local-only configuration:

```bash
bash scripts/verify-local-only.sh \
  --endpoint "$HCA_ENDPOINT" \
  --provider "$HCA_PROVIDER_NAME" \
  --model "$HCA_MODEL_NAME"
```

## Phase 5: Spawn Workers

```bash
bash scripts/spawn.sh 3
bash scripts/status.sh
```

For an OBS-friendly single tmux session with named panes for a project demo:

```bash
bash scripts/spawn-mm27-demo.sh --session local-team-demo --prefix mm27 --workspace /tmp/local-team-demo
```

The spawn script name is MM2.7-oriented, but the tmux layout pattern is usable with any profile prefix.

## Phase 6: Create Tasks

Manual CLI examples:

```bash
hermes kanban create "Research requirements" --assignee research-worker
hermes kanban create "Implement the script" --assignee coder-worker
hermes kanban create "Write launch copy" --assignee creative-worker
hermes kanban create "Test and verify" --assignee qa-worker
```

From the orchestrator pane:

```bash
tmux send-keys -t hca-1 "Break down this project into kanban tasks for coder, research, creative, and QA workers. Require QA PASS before final report." Enter
```

## Phase 7: Benchmark

```bash
bash scripts/benchmark.sh \
  --levels 1,2,3,4 \
  --endpoint "$HCA_ENDPOINT" \
  --model "$HCA_MODEL_NAME"
```

Report only numbers backed by the generated artifact directory.

## Phase 8: Monitor and Shutdown

```bash
bash scripts/status.sh
hermes kanban list
nvidia-smi -l 5

bash scripts/shutdown.sh
```

## Troubleshooting

### Backend not reachable

- Confirm server is running.
- Check `curl "$HCA_ENDPOINT/models"`.
- Ensure the URL includes `/v1` when required by your backend.

### Hermes returns model-not-found

- Your profile `model.default` does not match `/v1/models`.
- Re-run `setup.sh --model "$HCA_MODEL_NAME" --endpoint "$HCA_ENDPOINT" --force`.

### Workers use a remote provider

Run:

```bash
bash scripts/verify-local-only.sh --endpoint "$HCA_ENDPOINT" --provider "$HCA_PROVIDER_NAME" --model "$HCA_MODEL_NAME"
```

Fix every failing profile before claiming the team is fully local.

### OOM errors

- Reduce max context.
- Reduce concurrency.
- Lower backend memory utilization.
- Stop unrelated GPU processes before recording.

### Slow inference

- Warm the backend before the real run.
- Benchmark 1, 2, 3, and 4 workers to find your hardware's sweet spot.
- Check that backend logs show the optimized path for your chosen model/runtime.
