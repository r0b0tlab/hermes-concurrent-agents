---
name: hermes-concurrent-agents
description: GB10-first concurrent Hermes Agent fleets on vLLM/SGLang with tmux isolation, Kanban, and hca observability.
version: 2.0.0
---

# hermes-concurrent-agents

Control plane for many isolated Hermes sessions on one (or many) DGX Spark / GB10 hosts.

## Install

```bash
pip install -e ".[dev]"
```

## Daily use

```bash
hca init --preset gb10-vllm --model <served>
hca doctor && hca up --daemon
hca watch
```

## Skills / principles

1. Separate agent sessions (tmux + profiles); share only the inference engine.
2. Admit before claim; wave-limit ready work.
3. vLLM and SGLang are equal first-class backends.
4. Cluster fabric = passwordless SSH after NVIDIA playbooks (no NFS SQLite).
5. Observe with watch/peek/activity; attach only when needed.
6. Measure concurrency with `hca bench` — never invent universal N.
7. Short `delegate_task` bursts only; durable work = Kanban + slots.

## Docs

See `docs/` and `docs/plans/2026-07-12-hermes-agent-modernization.md`.
