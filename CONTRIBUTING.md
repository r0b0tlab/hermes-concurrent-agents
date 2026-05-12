# Contributing

Before opening a PR, run:

```bash
bash -n setup.sh scripts/*.sh
bash scripts/validate-docs.sh
bash scripts/benchmark.sh --dry-run --levels 1,2
bash scripts/smoke-kanban-flow.sh --dry-run
bash scripts/fault-injection-test.sh
```

For hardware benchmark changes, include an artifact bundle from:

```bash
bash scripts/benchmark.sh --levels 1,2,3,4,6 --endpoint http://127.0.0.1:8000/v1 --model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
```

Do not commit secrets, `.env` files, or private Hermes profile state.
