# Research Report Summary: Concurrent LLM Agents on GB10

Key findings from the research report on optimizing concurrent multi-agent systems on NVIDIA Grace Blackwell edge infrastructure.

## Hardware: NVIDIA GB10 Grace Blackwell

| Component | Spec | Impact |
|-----------|------|--------|
| GPU | Blackwell GB10, SM121, 5th-gen Tensor Cores | Native NVFP4 execution |
| CPU | 20-core ARM (10× X925 + 10× A725) | Big.LITTLE for agents vs inference |
| Memory | 128GB LPDDR5x unified, 273 GB/s | Single pool, no PCIe transfer |
| Storage | 4TB NVMe M.2 | Fast SQLite operations |
| Networking | 10GbE + ConnectX-7 (200Gb/s) | Multi-node scaling possible |

## Model: Nemotron 3 Nano 30B-A3B

- **Architecture:** Hybrid Mamba2-Transformer MoE
- **Total params:** ~31B | **Active per token:** 3B (10:1 sparsity)
- **Context window:** 256k tokens (Mamba enables linear scaling)
- **Multimodal:** Text, images, video, audio (unified perception)
- **NVFP4 footprint:** ~25-40GB

The 10:1 sparsity ratio is the key enabler — memory bandwidth to stream active weights is reduced by an order of magnitude.

## SM121 Kernel Requirement

**Critical finding:** Pre-built FlashInfer/CUTLASS wheels target SM120, causing hardware mismatches on SM121 (GB10).

| Implementation | Output tok/s | ITL | TTFT |
|---------------|-------------|-----|------|
| Unoptimized NVFP4 (SM120 kernels) | 18.91 | 51.62ms | 199.66ms |
| AWQ 4-bit (standard) | 24.93 | 39.01ms | 170.23ms |
| **Optimized NVFP4 (SM121 native)** | **35.60** | **<30ms** | **~150ms** |

**Fix:** Compile with `FLASHINFER_CUDA_ARCH_LIST="12.1f"` environment variable.

## MPS vs MIG

- **MIG (Multi-Instance GPU):** Hard partitions GPU + memory. Bad for shared model — fragments the 128GB pool.
- **MPS (Multi-Process Service):** Spatial sharing without partitioning. Multiple CUDA contexts share SMs concurrently. **Use this.**

## Inference Engine Selection

| Engine | Best For | Key Feature |
|--------|----------|-------------|
| **SGLang** | Multi-agent | RadixAttention (KV cache reuse for shared prompts) |
| vLLM | Single agent | PagedAttention, high throughput |
| llama.cpp | Memory-constrained | Quantized KV cache, fast TTFT |
| LitServe | Vision/classification | No LLM-specific optimizations |

**SGLang is recommended** for concurrent agents because RadixAttention reuses KV cache when workers share system prompts.

## Throughput Scaling

Theoretical max (single request): 182 tok/s (100% bandwidth utilization)

| Concurrent Agents | Expected Total TPS | Per-Agent TPS | Scaling Factor |
|-------------------|-------------------|---------------|----------------|
| 1 | ~35 | 35 | 1.0x |
| 2 | ~55-60 | 28-30 | 1.6x |
| 4 | ~80-95 | 20-24 | 2.5x |
| 6 | ~90-110 | 15-18 | 3.0x |
| 8 | ~85-100 | 11-13 | 2.7x (diminishing) |

Sub-linear scaling is expected due to KV cache memory overhead.

## Mathematical Model

```
T_max = M_bw / (P_active × b)
      = 273 GB/s / (3B × 0.5 bytes)
      = 182 tok/s (theoretical single-request max)

TPS_sys = T_max × B / (1 + α + β×L×B)
```

Where:
- M_bw = memory bandwidth (273 GB/s)
- P_active = active parameters (3B for MoE)
- b = bytes per parameter (0.5 for NVFP4)
- B = batch size (concurrent agents)
- α = fixed hardware latency
- β = marginal KV cache cost per agent
- L = context length

**Hermes reduces L** (via SQLite FTS5 memory), which lowers β, allowing TPS_sys to scale more efficiently with B.

## Memory Budget

```
Model weights:     25-40 GB (NVFP4)
KV cache:          40-60 GB (shared across agents)
OS + agents:       15-20 GB
Buffer:            10-15 GB
Total:             128 GB

Recommended: --mem-fraction-static 0.70 (leaves 30% for OS + agents)
```

## Key Optimization Flags

```bash
# SGLang launch flags for optimal concurrent performance
--mem-fraction-static 0.70    # NOT 0.85 — leave headroom
--max-model-len 32768         # NOT 256k — bound KV cache
--quantization modelopt_fp4   # Native NVFP4 on SM121
--enforce-eager               # Stability on SM121
--trust-remote-code           # Required for Nemotron
```

## Hermes Agent Advantages

| Metric | OpenClaw (JSONL) | Hermes (SQLite FTS5) |
|--------|-----------------|---------------------|
| Recall latency | 19,593 ms | 113 ms |
| Disk bloat | +213 KB/event | 0 KB |
| Memory fluctuation | 0 MB | -2.75 MB (compaction) |

Hermes compresses context algorithmically before invoking the LLM, directly reducing input tokens and enabling higher batch throughput.
