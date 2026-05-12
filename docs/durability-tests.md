# Durability and Fault-Injection Tests

The project uses two non-destructive validation paths:

```bash
bash scripts/smoke-kanban-flow.sh --dry-run
bash scripts/fault-injection-test.sh
```

For actual local fault injection:

```bash
bash scripts/fault-injection-test.sh --execute --prefix hca-fault
```

The harness exercises:

1. Benchmark artifact generation in dry-run mode.
2. Kanban smoke-flow command surface.
3. Prefix-scoped worker spawn.
4. Worker kill.
5. Worker restart.
6. Prefix-scoped shutdown.

## Safety

- Defaults are dry/non-destructive.
- Real execution uses the `hca-fault` prefix by default.
- It does not kill sessions outside the chosen prefix.
- It does not modify secrets or `.env` files.

## Manual GB10 durability checklist

For release evidence, add logs for:

- kill one worker mid-task and verify stale task recovery
- restart backend and verify workers fail clearly or recover
- run a 1/2/3/4/6 benchmark sweep without exceeding safe memory thresholds
- verify no two workers write the same output path without kanban dependency or worktree isolation
