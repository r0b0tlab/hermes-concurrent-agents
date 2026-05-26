# Educational Local Agent Team Video

This folder contains the public, recording-ready assets for the educational X video showing a fully local Hermes Agent team completing one project together. It pairs with `demos/mm27-local-agent-team/` (mission and acceptance check) and the optimized kanban graph script.

The plan is model-agnostic. Use any local OpenAI-compatible endpoint via the standard variables:

```bash
export HCA_ENDPOINT=http://127.0.0.1:8000/v1
export HCA_MODEL_NAME=your-served-model-name
export HCA_PROVIDER_NAME=local-vllm
```

## Files

- `SHOT_LIST.md` — per-scene seconds budget, what is on screen, on-screen overlay text, audio cue.
- `X_THREAD.md` — tweet thread template with the hero-metric placeholder.
- `post-production.sh` — ffmpeg recipe for remux, trim, overlay, watermark, X export.
- `caption.template.srt` — caption skeleton aligned to the shot list timings.

## Recording Flow

1. Pre-roll
   - `bash scripts/check-backend.sh --endpoint "$HCA_ENDPOINT" --model "$HCA_MODEL_NAME"`
   - `bash scripts/verify-local-only.sh --profiles demo-orchestrator,demo-coder,demo-research,demo-creative,demo-qa --endpoint "$HCA_ENDPOINT" --provider "$HCA_PROVIDER_NAME" --model "$HCA_MODEL_NAME" --smoke`
   - `bash scripts/benchmark.sh --levels 1,2,3,4 --endpoint "$HCA_ENDPOINT" --model "$HCA_MODEL_NAME" --output-dir benchmarks/video-pre`
2. Build the project workspace
   - `DEMO_WS=$PWD/demo-runs/local-agent-team/$(date -u +%Y%m%dT%H%M%SZ)`
   - `bash demos/mm27-local-agent-team/create-optimized-demo-tasks.sh --workspace "$DEMO_WS" --board demo-optimized --prefix demo`
3. Spawn the OBS-friendly tmux team
   - `bash scripts/spawn-mm27-demo.sh --session demo-optimized --workspace "$DEMO_WS" --prefix demo`
   - `tmux attach -t demo-optimized`
4. Record using the scene timing in `SHOT_LIST.md`.
5. Post-process: `bash demos/mm27-local-agent-team/video/post-production.sh --input <raw.mkv> --output final.mp4`.
6. Draft the thread from `X_THREAD.md`, plug in the measured hero metric, post.

## Design Principles

- The video teaches a pattern: kanban fan-out + orchestrator review/accept/reject removes dependency waiting on a single local GPU. Throughput is a supporting metric, not the thesis.
- Benchmark off camera. The on-screen metric card cites the measured `summary.csv` row.
- Hard local-only: `verify-local-only.sh` passes within 30 minutes of taking the shot.
- No device paths, no API keys, no internal IPs in any committed asset.
