# Deployment Guide

Step-by-step guide to deploying concurrent Hermes agents on unified-memory hardware.

## Phase 1: Hardware Setup

### Enable MPS (NVIDIA Multi-Process Service)

MPS lets multiple CUDA workloads share the GPU without hard partitioning. This is critical for concurrent inference.

```bash
# Start MPS daemon
sudo nvidia-cuda-mps-control -d

# Verify it's running
pgrep nvidia-cuda-mps-control

# Make it persistent (systemd)
sudo tee /etc/systemd/system/nvidia-mps.service << 'EOF'
[Unit]
Description=NVIDIA MPS Daemon
After=nvidia-persistenced.service

[Service]
Type=forking
ExecStart=/usr/bin/nvidia-cuda-mps-control -d
ExecStop=/usr/bin/nvidia-cuda-mps-control -q

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now nvidia-mps
```

### Verify GPU

```bash
nvidia-smi
# Should show your GPU with driver version and CUDA version
# GB10 shows SM121 compute capability
```

## Phase 2: Inference Backend

### Option A: SGLang (Recommended for Multi-Agent)

SGLang has RadixAttention which reuses KV cache when agents share system prompts.

```bash
# Pull the image
docker pull lmsysorg/sglang:latest

# Start with SM121-optimized settings
docker run -d --name sglang \
    --runtime nvidia --gpus all \
    -p 30000:30000 \
    -v ~/.cache/huggingface:/root/.cache/huggingface \
    -e FLASHINFER_CUDA_ARCH_LIST=12.1f \
    lmsysorg/sglang:latest \
    --model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 \
    --mem-fraction-static 0.70 \
    --max-model-len 32768 \
    --trust-remote-code \
    --quantization modelopt_fp4 \
    --port 30000 --host 0.0.0.0 --enforce-eager
```

Or use docker-compose:
```bash
docker compose -f config/sglang/docker-compose.yml up -d
```

### Option B: vLLM

```bash
docker compose -f config/vllm/docker-compose.yml up -d
```

### Option C: Ollama (Easiest)

```bash
# Install
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull nemotron:30b-a3b-nvfp4

# It auto-serves on port 11434
```

### Verify Backend

```bash
# Check models endpoint
curl http://localhost:30000/v1/models

# Test inference
curl http://localhost:30000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4","messages":[{"role":"user","content":"Say hello in one sentence."}]}'
```

## Phase 3: Profile Setup

```bash
cd hermes-concurrent-agents
bash setup.sh
```

This creates 5 isolated profiles: creative-worker, coder-worker, research-worker, qa-worker, orchestrator.

### Configure Model Per Profile

If all profiles use the same backend, configure once:

```bash
# Edit each profile's config
hermes -p creative-worker model  # interactive picker
hermes -p coder-worker model
# etc.
```

Or set via config:
```bash
# In ~/.hermes/profiles/creative-worker/config.yaml
model:
  default: nemotron-30b-nvfp4
  provider: custom:local-inference
  base_url: http://127.0.0.1:30000/v1
  api_key: local
```

## Phase 4: Spawn Workers

```bash
# Spawn 3 workers
bash scripts/spawn.sh 3

# Or with custom profiles
bash scripts/spawn.sh 2 --profiles creative-worker,coder-worker

# Check status
bash scripts/status.sh
```

## Phase 5: Create Tasks

### Manual (CLI)
```bash
hermes kanban create "Research topic A" --assignee research-worker
hermes kanban create "Build API endpoint" --assignee coder-worker
hermes kanban create "Write report" --assignee creative-worker --parent <research-id>
```

### Automatic (Gateway Dispatcher)
```bash
# Start gateway — it runs the kanban dispatcher every 60s
hermes gateway start
```

### From Orchestrator
```bash
# The orchestrator profile decomposes goals into kanban tasks
tmux send-keys -t hca-1 "Break down this goal into tasks for the team: [your goal]" Enter
```

## Phase 6: Monitor

```bash
# Quick status
bash scripts/status.sh

# Continuous health monitoring
bash scripts/health-monitor.sh

# Kanban board
hermes kanban list
hermes kanban watch

# GPU utilization
nvidia-smi -l 5
```

## Phase 7: Shutdown

```bash
# Graceful shutdown
bash scripts/shutdown.sh

# Stop inference backend
docker stop sglang-concurrent

# Stop MPS
sudo nvidia-cuda-mps-control -q
```

## Troubleshooting

### Workers not claiming tasks
- Check gateway is running: `hermes gateway status`
- Check kanban board has tasks: `hermes kanban list`
- Check profile names match: `hermes profile list`

### OOM errors
- Reduce `--mem-fraction-static` from 0.70 to 0.60
- Reduce `--max-model-len` from 32768 to 16384
- Reduce number of concurrent workers

### Slow inference
- Verify MPS is running: `pgrep nvidia-cuda-mps-control`
- Check GPU utilization: `nvidia-smi`
- Verify SM121 kernels (not SM120 fallback)

### Workers crashing
- Check logs: `tmux capture-pane -t <session> -p -S -50`
- Resume with: `hermes -p <profile> --continue`
- Check kanban for stale tasks: `hermes kanban list`
