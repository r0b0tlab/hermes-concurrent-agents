# Benchmarking (v2)

## Purpose

Measure the **knee** of concurrency for a given engine + model + host. Never publish universal worker counts.

## Command

```bash
hca bench --preset gb10-vllm --model <served> --levels 1,2,3,4,6,8
hca bench --engine sglang --endpoint http://127.0.0.1:30000/v1 --model <served>
hca bench --dry-run --levels 1,2,3   # structure only
```

Writes JSON under `<state_dir>/bench/` and prints:

- per-level success/fail, p50/p95 latency, throughput
- `recommended_max_sequences` + `knee_reason`

## Suites

1. Raw OpenAI chat sweep (implemented by `hca bench`)
2. Optional: Hermes one-shot worker sweep (future / scripts)
3. Optional: full Kanban+tmux fleet stress (manual)

## Applying results

Set in preset or config:

```toml
[capacity]
max_top_level_runs = <knee>
max_total_sequences = <knee>
max_wave_size = min(4, knee)
```

## Engines

Run benches **separately** for vLLM and SGLang; knees differ.
