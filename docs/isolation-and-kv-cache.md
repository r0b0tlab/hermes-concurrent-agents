# Isolation and KV cache

## Problem

Multiple agent sessions in one process (or shared session store) can collide on context and waste or corrupt KV usage. Continuous-batching engines share **weights**, not agent sessions.

## HCA rules

1. One worker process per run (tmux pane).
2. Separate Hermes profile / HERMES_HOME per slot when possible.
3. Fresh one-shot `hermes chat -q "work kanban task …"` workers preferred over long `--continue` recovery.
4. Warm slots may hold idle shells; they must not hold model context while idle.
5. Shared backend OK; shared agent session not OK.
6. Prefix caching benefits from stable role toolsets — avoid mid-run toolset mutation.

## Verification

- `hca doctor` checks dispatch_once spawn_fn contract
- `hca ps` shows one pid per active slot
- Engine metrics via capacity adapters (not proof of isolation alone)
