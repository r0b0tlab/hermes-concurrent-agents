# Benchmarking Guide

Use `scripts/benchmark.sh` to create reproducible artifact bundles for concurrency sweeps.

## CI / dry run

```bash
bash scripts/benchmark.sh --dry-run --levels 1,2
```

This validates artifact generation without requiring a GPU backend.

## GB10 measured run

`scripts/benchmark.sh` writes `summary.csv`, `metrics.json`, raw request/response JSON, worker logs, and per-level GPU samples. The summary includes throughput plus thermal/power/utilization/memory columns so public claims can cite both speed and operating conditions.

Example:

```bash
bash scripts/benchmark.sh \
  --levels 1,2,3,4,6 \
  --endpoint http://127.0.0.1:8000/v1 \
  --model your-served-model-name
```

Each run creates `benchmarks/YYYYMMDDTHHMMSSZ/` with:

- `env.txt` — environment manifest
- `summary.csv` — concurrency-level summary with throughput, power, utilization, memory, and thermals
- `metrics.json` — structured aggregate metrics
- `raw/` — per-worker request/response JSON
- `logs/` — per-worker curl/backend logs and `level-*-gpu-before.csv` / `level-*-gpu-samples.csv` / `level-*-gpu-after.csv` samples

The benchmark uses OpenAI-compatible response `usage` fields when the backend returns them. If usage is missing, token totals are reported as zero rather than guessed.

## Release evidence rule

Public speed claims must cite a benchmark artifact directory and distinguish:

- measured on GB10
- dry-run validation
- expected / estimated behavior
