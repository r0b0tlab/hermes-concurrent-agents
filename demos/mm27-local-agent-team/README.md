# MM2.7 Local Agent Team Demo

This demo mission shows a fully local Hermes Agent team completing one bounded project together. It is designed for MiniMax M2.7 NVFP4, but the pattern works with any local OpenAI-compatible model endpoint when profiles are configured with the chosen served model name.

## Project Mission

Build `local-agent-demo-dashboard`: a small static dashboard that summarizes a local multi-agent run.

The orchestrator assigns tasks, reviews outputs, accepts completed work, or rejects work for rework with improved instructions. All workers contribute to one shared project, not independent throwaway prompts.

## Roles

- `mm27-orchestrator`: creates kanban tasks, routes dependencies, reviews/accepts/rejects.
- `mm27-research`: writes requirements and acceptance criteria.
- `mm27-coder`: implements the dashboard generator and tests.
- `mm27-creative`: writes polished copy and demo caption.
- `mm27-qa`: runs tests and the acceptance checker; cannot PASS without evidence.

## Quick Run

```bash
export HCA_ENDPOINT=http://127.0.0.1:8000/v1
export HCA_MODEL_NAME=minimax-m27-nvfp4
export HCA_PROVIDER_NAME=local-mm27-vllm

bash scripts/check-backend.sh --endpoint "$HCA_ENDPOINT" --model "$HCA_MODEL_NAME"
bash scripts/setup-mm27-demo.sh --endpoint "$HCA_ENDPOINT" --model "$HCA_MODEL_NAME" --provider "$HCA_PROVIDER_NAME" --force
bash scripts/verify-local-only.sh --profiles mm27-orchestrator,mm27-coder,mm27-research,mm27-creative,mm27-qa --endpoint "$HCA_ENDPOINT" --provider "$HCA_PROVIDER_NAME" --model "$HCA_MODEL_NAME"

DEMO_WS=/home/r0b0tdgx/demo-runs/mm27-local-agent-team/$(date -u +%Y%m%dT%H%M%SZ)
bash demos/mm27-local-agent-team/create-kanban-tasks.sh --workspace "$DEMO_WS" --board mm27-demo
bash scripts/spawn-mm27-demo.sh --session mm27-demo --workspace "$DEMO_WS" --prefix mm27
```

Attach and record:

```bash
tmux attach -t mm27-demo
```

## Final Acceptance

After the team finishes:

```bash
bash demos/mm27-local-agent-team/acceptance-check.sh "$DEMO_WS/project"
```

The demo is complete only when the checker passes and `REPORT.md` records QA evidence.
