# Shot List — Local Agent Team Educational Video

Target length: 75-90 seconds (max 140s for X non-verified).
Aspect: 1920x1080 landscape.
Frame: tmux terminal, five panes tiled (orchestrator, research, coder, creative, qa).
Theme: dark background, light monospace, 14-16pt font.

## Thesis

> "On a single local GPU, you can run a real Hermes Agent team — research, code, creative, QA, orchestrator — completing one project with no cloud APIs and no idle waiting between agents."

## Hero Metric Card

- `<N>` = concurrent local agents (5 for this demo).
- `<TPS>` = aggregate tokens/second pulled from the off-camera `benchmarks/video-pre/<stamp>/summary.csv` row whose concurrency matches `<N>`.
- `<MODEL>` = served model name as returned by `/v1/models`.

The card is filled in post, not measured live on camera.

## Scenes

| Scene | t (s) | Visible | Overlay text | Audio |
|---|---|---|---|---|
| Hook | 0-3 | Black fade to 5-pane tmux grid, all panes idle. | `5 local agents. 1 GPU. 0 APIs.` | Music in |
| Stack | 3-10 | One pane shows `curl $HCA_ENDPOINT/models` returning the served model. | `Local OpenAI-compatible endpoint` | Music |
| Mission | 10-15 | Orchestrator pane shows the SPEC file path. | `Mission: build a local agent-team dashboard` | Music |
| Fan out | 15-25 | Orchestrator runs `create-optimized-demo-tasks.sh`. Four panes start working at once. | `Fan-out: research, coder, creative, QA — all start at t=0` | Music |
| Work | 25-55 | Cuts between panes producing `SPEC_APPENDIX.md`, dashboard `index.html`, `DEMO_CAPTION.md`, `QA_CHECKLIST.md`. | `No worker is waiting on another` | Music |
| Review | 55-70 | Orchestrator pane writes `INTEGRATION_REVIEW.md`; QA runs `acceptance-check.sh`. | `Orchestrator: accept / reject / rework` | Music |
| Result | 70-82 | Cut to generated dashboard HTML in a browser (r0b0tlab colors visible). | `<N> local agents. <TPS> tok/s aggregate.` | Music swell |
| Close | 82-90 | Cut back to terminal; show `REPORT.md` final PASS; watermark and repo link. | `github.com/r0b0tlab/hermes-concurrent-agents` `@mr-r0b0t` | Music tail |

## Pane Roles on Screen

- Pane 0 (top-left): `demo-orchestrator` — runs commands, reviews artifacts, writes `REPORT.md`.
- Pane 1 (top-right): `demo-research` — short visible `SPEC_APPENDIX.md` updates.
- Pane 2 (mid-left): `demo-coder` — visible file creation (`src/build_dashboard.py`, `tests/...`).
- Pane 3 (mid-right): `demo-creative` — visible `DEMO_CAPTION.md` lines, copy snippets.
- Pane 4 (bottom): `demo-qa` — visible `QA_CHECKLIST.md`, then `acceptance-check.sh` PASS output.

## Worker Briefing Rules (for video readability)

Workers should:
- Print `STARTED: <task-id>` on claim.
- Print `DONE: <task-id> -> <artifact-path>` on completion.
- Avoid printing long internal reasoning; keep visible output tight.

## Retake Decision Table

| Condition | Action |
|---|---|
| QA passes within scene budget | Use the take. |
| QA fails but T7 rework fits in budget | Use the take. The video shows the loop in action, which is on-thesis. |
| Worker hangs past pane budget | Cut and re-roll. Capture the SHM/JIT failure mode in a follow-up reel. |
| Endpoint returns 4xx/5xx on camera | Cut. Re-run pre-roll. |
| Total length exceeds 110 seconds | Cut. Trim worker output and re-roll. |

## What the Viewer Should Be Able To Say at t=90s

> "A single local model server feeds five Hermes Agent profiles. They fan out through a kanban graph so research, coder, creative, and QA all start at once. The orchestrator reviews and accepts each lane, QA enforces the contract, and the whole project lands without any cloud call."

If a sample viewer cannot say this after watching, the cut is not done.
