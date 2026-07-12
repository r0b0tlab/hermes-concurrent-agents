# Tuning guide (v2)

## Measure first

```bash
hca bench --preset gb10-vllm --model <id> --levels 1,2,3,4,6,8
```

Apply knee to:

```toml
[capacity]
max_top_level_runs = <knee>
max_total_sequences = <knee>
max_wave_size = 4
launch_stagger_seconds = 1.5
```

## Engine knobs (document, do not hardcode)

### vLLM
- `--gpu-memory-utilization`
- `--max-num-seqs` / `--max-num-batched-tokens`
- `--enable-prefix-caching` / chunked prefill
- tool-call parser flags for agent models

### SGLang
- port 30000
- NVFP4: `--quantization modelopt_fp4` when applicable

Always follow current [NVIDIA playbooks](nvidia-playbooks.md).

## Role slots

Fewer high-quality coder slots beat many thrashing ones. Keep orchestrator toolsets non-implementing.

## UMA

Prefer lowering concurrency over automatic `drop_caches`. Manual flush only as recovery.
