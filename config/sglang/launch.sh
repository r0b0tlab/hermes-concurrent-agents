#!/usr/bin/env bash
set -euo pipefail

# Launch SGLang inference backend for concurrent Hermes agents
# Alternative to docker-compose for direct docker run.
#
# Aligned with the NVIDIA DGX Spark SGLang playbook (CUDA 13 image, flashinfer):
#   https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/sglang
# NVFP4 models additionally require: EXTRA_ARGS="--quantization modelopt_fp4"

MODEL_PATH="${MODEL_PATH:?set MODEL_PATH to a local path or HF id}"
MODEL_NAME="${MODEL_NAME:?set MODEL_NAME to the served model name}"
PORT="${PORT:-30000}"
MEM_FRAC="${MEM_FRAC:-0.75}"
MAX_LEN="${MAX_LEN:-65536}"           # Hermes requires >=64k context for tool use
TOOL_PARSER="${TOOL_PARSER:-qwen}"    # model-specific; required for Hermes tool calling
IMAGE="${IMAGE:-lmsysorg/sglang:latest-cu130}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

echo "Starting SGLang inference backend..."
echo "  Model: $MODEL_PATH (served as $MODEL_NAME)"
echo "  Port: $PORT"
echo "  Memory fraction: $MEM_FRAC"
echo "  Max model length: $MAX_LEN"

# Stop existing container
docker stop sglang-concurrent 2>/dev/null || true
docker rm sglang-concurrent 2>/dev/null || true

# shellcheck disable=SC2086
docker run -d \
    --name sglang-concurrent \
    --gpus all \
    -p "${PORT}:30000" \
    -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
    -e "HF_TOKEN=${HF_TOKEN:-}" \
    "$IMAGE" \
    python3 -m sglang.launch_server \
    --model-path "$MODEL_PATH" \
    --served-model-name "$MODEL_NAME" \
    --host 0.0.0.0 \
    --port 30000 \
    --trust-remote-code \
    --attention-backend flashinfer \
    --mem-fraction-static "$MEM_FRAC" \
    --context-length "$MAX_LEN" \
    --tool-call-parser "$TOOL_PARSER" \
    --enable-metrics \
    $EXTRA_ARGS

echo ""
echo "SGLang starting... health check in ~120s"
echo "Verify: curl http://localhost:${PORT}/v1/models && hca doctor --tools"
echo "Logs:   docker logs -f sglang-concurrent"
