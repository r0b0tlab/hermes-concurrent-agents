#!/usr/bin/env bash
set -euo pipefail

# WARNING: SGLang is EXPERIMENTAL on GB10 (SM121).
# sgl_kernel has no prebuilt sm121 binaries — requires building from source with CUDA 13.
# Use vLLM config (config/vllm/) instead for production.
# See: https://github.com/sgl-project/sglang/issues/11658

# Launch SGLang inference backend for concurrent agents
# Alternative to docker-compose for direct docker run

MODEL="${MODEL:-nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4}"
PORT="${PORT:-30000}"
MEM_FRAC="${MEM_FRAC:-0.70}"
MAX_LEN="${MAX_LEN:-32768}"
IMAGE="${IMAGE:-lmsysorg/sglang:latest}"

echo "Starting SGLang inference backend..."
echo "  Model: $MODEL"
echo "  Port: $PORT"
echo "  Memory fraction: $MEM_FRAC"
echo "  Max model length: $MAX_LEN"

# Stop existing container
docker stop sglang-concurrent 2>/dev/null || true
docker rm sglang-concurrent 2>/dev/null || true

docker run -d \
    --name sglang-concurrent \
    --runtime nvidia \
    --gpus all \
    -p "${PORT}:30000" \
    -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
    -e "FLASHINFER_CUDA_ARCH_LIST=12.1f" \
    "$IMAGE" \
    --model "$MODEL" \
    --mem-fraction-static "$MEM_FRAC" \
    --max-model-len "$MAX_LEN" \
    --trust-remote-code \
    --quantization modelopt_fp4 \
    --port 30000 \
    --host 0.0.0.0 \
    --enforce-eager

echo ""
echo "SGLang starting... health check in 120s"
echo "Verify: curl http://localhost:${PORT}/v1/models"
echo "Logs:   docker logs -f sglang-concurrent"