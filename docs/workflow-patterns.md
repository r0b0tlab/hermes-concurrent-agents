# Workflow patterns (v2)

## Large goal on one Spark

```bash
hca init --preset gb10-vllm --model <id>
hca doctor && hca up --daemon
hca task swarm "Ship release X"   # or hermes kanban swarm
hca watch
hca explain <task>   # if waiting
hca drain && hca down
```

## Parallel research + code

```bash
hca task add "Survey approaches" --role research
hca task add "Implement API" --role coder --repo ~/src/app
hca task add "Review PR" --role qa
hca up
```

## Subagents vs Kanban children

| Need | Use |
|---|---|
| Short parallel burst seconds–minutes | `delegate_task` (budgeted) |
| Durable multi-hour work | Kanban child + tmux slot |
| Human checkpoint | Kanban block + comment |

## Cluster fan-out

```bash
hca init --preset gb10-cluster-vllm --model <id>
hca cluster nodes add a b c
hca cluster doctor && hca cluster nodes up
hca up --role control
# submit on control; placement SSHes to nodes
```
