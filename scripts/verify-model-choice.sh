#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
check(){
  local desc="$1"
  shift
  if "$@"; then
    echo "[pass] $desc"
  else
    echo "[fail] $desc" >&2
    fail=1
  fi
}

check "generic profile template exists" test -f config/profile-template.yaml
check "MM2.7 profile template exists" test -f config/mm27/profile-template.yaml
check "MM2.7 demo setup script exists" test -x scripts/setup-mm27-demo.sh
check "model-choice backend checker exists" test -x scripts/check-backend.sh
check "local-only verifier exists" test -x scripts/verify-local-only.sh
check "MM2.7 demo doc exists" test -f docs/mm27-gb10-demo.md

check "default profile template uses caller-selectable model placeholder" grep -Eq 'HCA_MODEL_NAME|<served-model-name>|__MODEL_NAME__' config/profile-template.yaml
check "default profile template is not hardcoded to Nemotron" bash -c "! grep -q 'NVIDIA-Nemotron' config/profile-template.yaml"
check "active docs do not recommend Marlin as the default backend" bash -c "! grep -RniE 'VLLM_NVFP4_GEMM_BACKEND=marlin|--moe-backend marlin|Set VLLM_NVFP4_GEMM_BACKEND=marlin' README.md SKILL.md docs/deployment-guide.md config/profile-template.yaml config/vllm/docker-compose.yml"
check "MM2.7 docs use FlashInfer-CUTLASS, not Marlin" bash -c "grep -q 'flashinfer-cutlass' docs/mm27-gb10-demo.md && ! grep -qi 'marlin' docs/mm27-gb10-demo.md"
check "benchmark help supports arbitrary model choice" bash -c "bash scripts/benchmark.sh --help | grep -q -- '--model NAME'"

exit "$fail"
