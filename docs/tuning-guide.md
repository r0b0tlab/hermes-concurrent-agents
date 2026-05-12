# Performance Tuning Guide

How to find the optimal concurrency level and configuration for your hardware.

## Core Principle: Optimal, Not Maximum

The goal is **peak total tok/s**, not 100% GPU utilization. Running at 100% memory
usage causes swapping, OOM kills, and degraded latency. The sweet spot is typically
70-80% memory utilization.

## Step 1: Establish Single-Agent Baseline

Before scaling, measure your single-agent throughput:

```bash
# Start inference backend
# Start a single worker
bash scripts/spawn.sh 1

# Send a benchmark prompt
time hermes chat -q "Write a 500-word analysis of AI safety."
```

Expected baselines (Nemotron 30B-A3B NVFP4 on GB10):
- Unoptimized: ~19 tok/s
- SM121-optimized: ~35 tok/s
- With SGLang RadixAttention: ~35+ tok/s

## Step 2: Find Your Concurrency Sweet Spot

Run the benchmark script:
```bash
bash scripts/benchmark.sh
```

Or manually test each level:
```bash
for n in 1 2 3 4 6; do
    echo "=== Concurrency $n ==="
    bash scripts/spawn.sh $n
    # Send same prompt to all workers
    # Measure total completion time
    bash scripts/shutdown.sh
    sleep 5
done
```

### What to Look For

| Metric | Good | Bad |
|--------|------|-----|
| Per-agent tok/s | Drops gracefully | Drops sharply after N |
| Total tok/s | Peaks at some N | Plateaus or decreases |
| ITL (inter-token latency) | < 100ms | > 200ms |
| Memory usage | < 80% | > 90% (swapping imminent) |

## Step 3: Memory Budget Calculation

For 128GB unified memory:

```
Available for inference: 128GB × 0.70 = 89.6GB (with --mem-fraction-static 0.70)

Model weights (NVFP4, 30B-A3B): ~25GB
KV cache (shared across agents): ~40-60GB (depends on context length and concurrency)
Remaining for OS/agents: ~15-20GB

KV cache per concurrent agent:
  ~2GB at 8k context
  ~4GB at 16k context
  ~8GB at 32k context
```

**Formula:** Max agents ≈ (Available - Model_Weights) / KV_per_agent

Example: (89.6 - 25) / 4 = ~16 agents at 16k context (theoretical)
Practical limit: 4-6 agents (due to batching overhead and latency)

## Step 4: Model Selection

Not all tasks need the same model:

| Task Type | Recommended Model | Why |
|-----------|------------------|-----|
| Research/search | Fast 7-8B model | Low latency, high throughput |
| Code generation | Code-specialized 14-32B | Quality matters |
| Creative writing | 30B+ or MoE | Needs creativity and coherence |
| QA/testing | Fast 7-14B model | Speed over creativity |

Multi-model setup:
```yaml
# research-worker: fast model
model:
  default: qwen3-8b
  provider: custom:local-ollama
  base_url: http://127.0.0.1:11434/v1

# creative-worker: big model
model:
  default: nemotron-30b-nvfp4
  provider: custom:local-vllm
  base_url: http://127.0.0.1:30000/v1
```

## Step 5: KV Cache Management

Context length directly impacts memory usage and throughput:

- **Shorter context = more concurrent agents**
- **SGLang RadixAttention** reuses KV cache for shared prefixes (system prompts)
- **Hermes SQLite memory** reduces input tokens by fetching context algorithmically

Best practices:
1. Keep system prompts identical across workers (enables KV reuse)
2. Use Hermes memory instead of re-sending history
3. Set `--max-model-len 32768` (not 256k) to bound KV cache
4. Enable context compression in profile config

## Step 6: Backend Comparison

| Feature | SGLang | vLLM | Ollama |
|---------|--------|------|--------|
| Continuous batching | Yes | Yes | Limited |
| RadixAttention (KV reuse) | Yes | No | No |
| PagedAttention | Yes | Yes | No |
| SM121 optimization | Community builds | Community builds | N/A |
| Setup complexity | Medium | Medium | Easy |
| Best for | Multi-agent | Single agent | Quick testing |

**Recommendation:** SGLang for multi-agent setups (RadixAttention is the differentiator).

## Step 7: ARM-Specific Optimizations (GB10)

The GB10 uses ARM Cortex-X925 (performance) + A725 (efficiency) cores:

```bash
# Pin inference backend to performance cores
taskset -c 0-9 docker run ...  # X925 cores

# Pin agent processes to efficiency cores
taskset -c 10-19 hermes -p worker-1
```

This prevents agent overhead from competing with inference for CPU time.

## Quick Reference: What to Tune

| If you see... | Try this... |
|---------------|-------------|
| OOM at concurrency 4 | Reduce mem-fraction-static to 0.60 |
| High ITL (>200ms) | Reduce max-model-len or concurrency |
| Low total TPS | Increase concurrency gradually |
| Workers timing out | Increase gateway_timeout |
| Context explosion | Enable compression, enforce SOUL.md rules |
| GPU idle between requests | Increase concurrency (GPU is underutilized) |
