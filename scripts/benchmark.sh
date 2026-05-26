#!/usr/bin/env bash
set -euo pipefail

# Reproducible concurrency benchmark for Hermes/local OpenAI-compatible backends.
# Produces an artifact bundle with env manifest, raw JSONL responses, worker logs,
# metrics.json, and summary.csv. Uses real response usage tokens when available.

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info(){ echo -e "${BLUE}[info]${NC} $*"; }
ok(){ echo -e "${GREEN}[ok]${NC} $*"; }
warn(){ echo -e "${YELLOW}[warn]${NC} $*"; }
err(){ echo -e "${RED}[error]${NC} $*" >&2; }
usage(){
  cat <<'USAGE'
Usage: benchmark.sh [OPTIONS]

Options:
  --endpoint URL       OpenAI-compatible base URL (default: http://127.0.0.1:8000/v1)
  --model NAME         Model name to send in requests
  --levels CSV         Concurrency levels (default: 1,2,3,4,6)
  --prompt TEXT        Benchmark prompt
  --output-dir DIR     Parent output directory (default: benchmarks)
  --timeout SEC        Per-level timeout (default: 300)
  --dry-run            Do not call backend; generate deterministic synthetic artifacts
  -h, --help           Show this help
USAGE
}

ENDPOINT="http://127.0.0.1:8000/v1"
MODEL="${HCA_MODEL_NAME:-local-model}"
LEVELS="1,2,3,4,6"
PROMPT="Write a detailed 500-word analysis of the benefits and risks of autonomous AI agents in software development. Include specific examples."
OUT_PARENT="benchmarks"
TIMEOUT=300
DRY_RUN=false

sample_gpu(){
  local out="$1"
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=timestamp,name,temperature.gpu,power.draw,utilization.gpu,memory.used,memory.total \
      --format=csv,noheader,nounits > "$out" 2>/dev/null || true
  fi
}

gpu_sampler(){
  local out="$1" interval="${2:-1}"
  : > "$out"
  while true; do
    sample_gpu /tmp/hca-gpu-sample.$$
    cat /tmp/hca-gpu-sample.$$ >> "$out" 2>/dev/null || true
    rm -f /tmp/hca-gpu-sample.$$
    sleep "$interval"
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --levels) LEVELS="$2"; shift 2 ;;
    --prompt) PROMPT="$2"; shift 2 ;;
    --output-dir) OUT_PARENT="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "Unknown option: $1"; usage; exit 1 ;;
  esac
done

IFS=',' read -r -a LEVEL_ARRAY <<< "$LEVELS"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT_DIR="${OUT_PARENT%/}/${STAMP}"
mkdir -p "$OUT_DIR/raw" "$OUT_DIR/logs"

info "Writing benchmark artifacts to $OUT_DIR"
{
  echo "timestamp_utc=$STAMP"
  echo "git_commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "kernel=$(uname -srvmo 2>/dev/null || echo unknown)"
  echo "hermes=$(hermes --version 2>/dev/null || echo unavailable)"
  echo "endpoint=$ENDPOINT"
  echo "model=$MODEL"
  echo "levels=$LEVELS"
  echo "dry_run=$DRY_RUN"
  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia_smi="
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null || true
  fi
} > "$OUT_DIR/env.txt"

if [[ "$DRY_RUN" == false ]]; then
  if ! command -v curl >/dev/null 2>&1; then err "curl is required"; exit 1; fi
  if ! command -v python3 >/dev/null 2>&1; then err "python3 is required"; exit 1; fi
  if ! curl -fsS --max-time 5 "$ENDPOINT/models" >/dev/null; then
    err "Backend not reachable at $ENDPOINT/models. Use --dry-run for CI/static validation."
    exit 2
  fi
fi

printf "level,total_elapsed_s,requests,successes,failures,prompt_tokens,completion_tokens,total_tokens,total_tps,pre_max_temp_c,post_max_temp_c,run_max_temp_c,pre_avg_power_w,post_avg_power_w,run_avg_power_w,pre_avg_gpu_util_pct,post_avg_gpu_util_pct,run_avg_gpu_util_pct,run_max_mem_used_mb\n" > "$OUT_DIR/summary.csv"

for level in "${LEVEL_ARRAY[@]}"; do
  info "Level $level"
  LEVEL_DIR="$OUT_DIR/raw/level-$level"
  mkdir -p "$LEVEL_DIR"
  sample_gpu "$OUT_DIR/logs/level-${level}-gpu-before.csv"
  SAMPLER_PID=""
  if command -v nvidia-smi >/dev/null 2>&1; then
    gpu_sampler "$OUT_DIR/logs/level-${level}-gpu-samples.csv" 1 &
    SAMPLER_PID="$!"
  fi
  START=$(python3 - <<'PY'
import time; print(time.time())
PY
)

  pids=()
  for i in $(seq 1 "$level"); do
    REQ="$LEVEL_DIR/request-$i.json"
    RESP="$LEVEL_DIR/response-$i.json"
    LOG="$OUT_DIR/logs/level-${level}-worker-${i}.log"
    python3 - "$MODEL" "$PROMPT" > "$REQ" <<'PY'
import json, sys
model, prompt = sys.argv[1], sys.argv[2]
print(json.dumps({
  "model": model,
  "messages": [{"role": "user", "content": prompt}],
  "temperature": 0.2,
  "max_tokens": 900,
}))
PY
    if [[ "$DRY_RUN" == true ]]; then
      python3 - "$level" "$i" > "$RESP" <<'PY' &
import json, sys, time
level, i = int(sys.argv[1]), int(sys.argv[2])
time.sleep(0.05)
completion = 700 + i
print(json.dumps({"id": f"dry-{level}-{i}", "object": "chat.completion", "choices": [{"message": {"role": "assistant", "content": "dry run"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 55, "completion_tokens": completion, "total_tokens": 55+completion}}))
PY
      pids+=("$!")
    else
      {
        echo "start $(date -u +%FT%TZ)"
        curl -fsS --max-time "$TIMEOUT" "$ENDPOINT/chat/completions" \
          -H "Content-Type: application/json" \
          --data-binary "@$REQ" > "$RESP"
        echo "end $(date -u +%FT%TZ)"
      } > "$LOG" 2>&1 &
      pids+=("$!")
    fi
  done

  failures=0
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then failures=$((failures + 1)); fi
  done

  if [[ -n "$SAMPLER_PID" ]]; then
    kill "$SAMPLER_PID" >/dev/null 2>&1 || true
    wait "$SAMPLER_PID" 2>/dev/null || true
  fi
  sample_gpu "$OUT_DIR/logs/level-${level}-gpu-after.csv"
  END=$(python3 - <<'PY'
import time; print(time.time())
PY
)
  python3 - "$OUT_DIR" "$level" "$START" "$END" "$failures" <<'PY'
import csv, glob, json, os, statistics, sys
out_dir, level, start, end, failures = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5])
responses = sorted(glob.glob(os.path.join(out_dir, "raw", f"level-{level}", "response-*.json")))

def gpu_stats(path):
    rows = []
    if not os.path.exists(path):
        return {}
    for line in open(path, encoding="utf-8"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        def num(x):
            try: return float(x)
            except Exception: return None
        rows.append({"timestamp": parts[0], "name": parts[1], "temp_c": num(parts[2]), "power_w": num(parts[3]), "util_pct": num(parts[4]), "mem_used_mb": num(parts[5]), "mem_total_mb": num(parts[6])})
    vals = lambda key: [r[key] for r in rows if r.get(key) is not None]
    return {
        "gpus": rows,
        "max_temp_c": max(vals("temp_c"), default=None),
        "avg_power_w": statistics.fmean(vals("power_w")) if vals("power_w") else None,
        "avg_gpu_util_pct": statistics.fmean(vals("util_pct")) if vals("util_pct") else None,
        "mem_used_mb": max(vals("mem_used_mb"), default=None),
        "mem_total_mb": sum(vals("mem_total_mb")) if vals("mem_total_mb") else None,
    }

pre_gpu = gpu_stats(os.path.join(out_dir, "logs", f"level-{level}-gpu-before.csv"))
post_gpu = gpu_stats(os.path.join(out_dir, "logs", f"level-{level}-gpu-after.csv"))
run_gpu = gpu_stats(os.path.join(out_dir, "logs", f"level-{level}-gpu-samples.csv"))
prompt = completion = total = successes = 0
records = []
for path in responses:
    try:
        data = json.load(open(path, encoding="utf-8"))
        if "error" in data:
            failures += 1
            continue
        usage = data.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        tt = int(usage.get("total_tokens") or (pt + ct))
        prompt += pt; completion += ct; total += tt; successes += 1
        records.append({"path": path, "usage": usage})
    except Exception as exc:
        failures += 1
        records.append({"path": path, "error": str(exc)})
elapsed = max(end - start, 0.001)
tps = total / elapsed if total else 0.0
summary = {"level": level, "elapsed_s": elapsed, "requests": level, "successes": successes, "failures": failures, "prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total, "total_tps": tps, "gpu_before": pre_gpu, "gpu_after": post_gpu, "gpu_during": run_gpu, "responses": records}
with open(os.path.join(out_dir, f"level-{level}.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)
def fmt(v):
    return "" if v is None else f"{v:.3f}" if isinstance(v, float) else v
with open(os.path.join(out_dir, "summary.csv"), "a", encoding="utf-8", newline="") as f:
    csv.writer(f).writerow([
        level, f"{elapsed:.3f}", level, successes, failures, prompt, completion, total, f"{tps:.3f}",
        fmt(pre_gpu.get("max_temp_c")), fmt(post_gpu.get("max_temp_c")), fmt(run_gpu.get("max_temp_c")),
        fmt(pre_gpu.get("avg_power_w")), fmt(post_gpu.get("avg_power_w")), fmt(run_gpu.get("avg_power_w")),
        fmt(pre_gpu.get("avg_gpu_util_pct")), fmt(post_gpu.get("avg_gpu_util_pct")), fmt(run_gpu.get("avg_gpu_util_pct")),
        fmt(run_gpu.get("mem_used_mb")),
    ])
PY
done

python3 - "$OUT_DIR" <<'PY'
import glob, json, os, sys
out = sys.argv[1]
levels = []
for path in sorted(glob.glob(os.path.join(out, "level-*.json"))):
    levels.append(json.load(open(path, encoding="utf-8")))
report = {"artifact_dir": out, "levels": levels}
with open(os.path.join(out, "metrics.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2)
with open(os.path.join(out, "README.md"), "w", encoding="utf-8") as f:
    f.write("# Benchmark Artifact Bundle\n\n")
    f.write("Generated by `scripts/benchmark.sh`.\n\n")
    f.write("Files:\n- `env.txt` environment manifest\n- `summary.csv` level summary with throughput, power, utilization, memory, and thermal columns\n- `metrics.json` combined JSON metrics\n- `raw/` request/response JSON\n- `logs/` per-worker logs plus per-level GPU before/during/after samples\n")
PY

ok "Benchmark complete: $OUT_DIR"
echo "Summary: $OUT_DIR/summary.csv"
