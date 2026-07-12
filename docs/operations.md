# Operations

## Day-1 single Spark

```bash
pip install -e ".[dev]"
# start vLLM :8000 or SGLang :30000 per NVIDIA playbook
hca init --preset gb10-vllm --model <id>
hca doctor
hca up --daemon   # or one-shot hca up
hca watch
hca task add "Do the thing" --assignee coder-worker
```

## Safe stop

```bash
hca drain              # stop admits
hca down               # drain; keep slots
hca down --kill        # signal running panes
hca down --kill --slots  # also destroy warm slots
hca drain --clear      # re-enable admits
```

## Observe

```bash
hca ps
hca peek hca-gb10-coder-01
hca activity --follow
hca transcript <task-or-run>
hca logs <run> --follow
hca explain waiting
```

## Capacity

```bash
hca plan --json
hca bench --engine vllm --model <id> --levels 1,2,3,4,6,8
hca bench --engine sglang --endpoint http://127.0.0.1:30000/v1 --model <id>
```

Set `capacity.max_top_level_runs` / `max_total_sequences` from the measured knee — never invent universal N.

## UMA recovery (manual)

```bash
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
```

Do not automate mid-fleet unless explicitly configured.

## Troubleshooting

| Symptom | Check |
|---|---|
| doctor FAIL models | Engine up? model id match `/v1/models`? |
| dispatch skipped | `hca explain x` — drain? capacity? backend healthy? |
| tmux missing | `hca up` warm slots; socket name in preset |
| cluster ssh FAIL | BatchMode, same username, NVIDIA discover-sparks |
| attach hangs | use peek first; attach is interactive |
