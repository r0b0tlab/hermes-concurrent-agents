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
disk_min_free_gb = 20
disk_resume_free_gb = 25
disk_strict_percent = false
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

## Separate limits

- `max_top_level_runs` counts high-level HCA missions, not worker attempts.
- `max_total_sequences` limits worker/subagent credits.
- `RunSpec.concurrency` limits live owned workers in one mission, including
  replacements.
- `max_supervisor_replacements` bounds infrastructure recovery and does not
  replace Hermes' consecutive task/model failure circuit breaker.
- `max_disk_mb` must fit in current free space after the absolute reserve.
