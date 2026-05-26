# X Thread Template — Local Agent Team Educational Video

Replace every `<placeholder>` with measured values from the off-camera benchmark artifact and the recording. Do not post until every placeholder is filled.

---

## Tweet 1 — Hook (attach the MP4)

> 5 Hermes Agents. 1 local GPU. 0 cloud APIs.
>
> A real agent team — research, code, creative, QA, orchestrator — finishes one project at the same time, on `<MODEL_NAME>`.
>
> Aggregate throughput: `<TPS>` tok/s across `<N>` concurrent agents.
>
> Repo: github.com/r0b0tlab/hermes-concurrent-agents

## Tweet 2 — Why this matters

> The bottleneck for local agent teams is usually waiting, not GPU. If research blocks coder blocks QA, you're paying for one agent at a time.
>
> The pattern in the video uses a kanban graph that fans out four lanes immediately. Orchestrator only fan-in for review.

## Tweet 3 — Stack

> Model: `<MODEL_NAME>`
> Runtime: `<RUNTIME>` (vLLM / SGLang / Ollama)
> Endpoint: local OpenAI-compatible
> Scheduler: kanban (SQLite), shared by all five profiles
> Recording: tmux + OBS, no cloud calls

## Tweet 4 — Architecture (attach diagram)

> Five Hermes profiles, one model server, one shared kanban.db.
>
> T1-T4 parallel lanes -> T5 orchestrator review -> T6 QA -> T7 rework gate -> T8 final report.

## Tweet 5 — Repos and docs

> Repo: github.com/r0b0tlab/hermes-concurrent-agents
> Demo flow: demos/mm27-local-agent-team
> Hermes Agent: github.com/NousResearch/hermes-agent

## Tweet 6 — Caveats

> Numbers above are measured on `<HARDWARE>` from `benchmarks/video-pre/<STAMP>/summary.csv`.
> Your local mileage depends on quantization, KV dtype, MoE backend, and OBS overhead.

## Tweet 7 — Pin and credit

> By @mr-r0b0t — r0b0tlab.
> MIT-licensed. Issues and PRs welcome.

---

## Posting Notes

- US weekday window: 9-11 ET or 13-15 ET.
- Pin tweet 1 for at least 24 hours.
- Reply actively for the first 30 minutes to compound the algorithm pickup.
- Cross-post: LinkedIn, r/LocalLLaMA, repo Discussions.
- If any benchmark caveat applies (different hardware, different model), put it in tweet 6 before tweet 5 so it is read before someone questions the metric.
