# hermes-concurrent-agents

> **By [@mr-r0b0t on X](https://x.com/mr_r0b0t) — [r0b0tlab](https://github.com/r0b0tlab)**

Turn one goal into a small, bounded, supervised team built on
[Hermes Agent](https://github.com/NousResearch/hermes-agent) Kanban, profiles,
sessions, workspaces, and plugins. HCA adds pre-claim admission, concrete worker
slots, exact process ownership, restart reconciliation, bounded review/rework,
and one evidence-backed result.

> **Alpha, single-host control plane.** The authoritative Kanban board and HCA
> workers must remain on one host. A model endpoint may be remote through an
> ordinary Hermes profile. Remote **agent** placement is unsupported and fails
> before SSH side effects; see [cluster scope](docs/gb10-cluster.md).

## Supported baseline

- Linux with Python 3.11 or 3.12 and `tmux`
- Hermes Agent `0.18.2` / `2026.7.7.2` for the required stable contract lane
- Any model/provider already configured in the selected Hermes profile
- Generic Linux operation without CUDA, NVML, or endpoint telemetry
- Optional GB10-aware admission and vLLM/SGLang telemetry adapters

See the test-generated [support matrix](docs/support-matrix.md) for precise
boundaries. HCA does **not** provision models, copy provider credentials, replace
Hermes tools, normalize providers, or own endpoint fallback.

## Install

```bash
git clone https://github.com/r0b0tlab/hermes-concurrent-agents.git
cd hermes-concurrent-agents
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .

hermes --version
hca version
```

For development, install `.[dev]`. Hermes and `tmux` must already be available
on `PATH`; configure and authenticate Hermes before initializing HCA.

## Five-minute quickstart

Initialize bounded worker profiles from an existing Hermes profile:

```bash
hca init \
  --preset generic-linux \
  --model <served-model-id> \
  --source-profile default

hca doctor
```

Run one goal and collect one durable result:

```bash
hca run \
  --source-profile default \
  --project "$PWD" \
  --acceptance "Implementation and focused tests pass" \
  --review auto \
  "Implement the requested change and verify it"

hca run-status <run-id>
hca collect <run-id>
```

One-step work uses one worker. Fan-out requires both multiple acceptance
criteria and explicit `--independent-criteria`; concurrency remains bounded by
concrete slots and admission capacity.

```bash
hca run \
  --source-profile default \
  --acceptance "Research the compatibility boundary" \
  --acceptance "Audit the packaging and CI boundary" \
  --independent-criteria \
  --concurrency 2 \
  "Produce one integrated release-readiness report"
```

If a run needs human input, answer only its recorded question:

```bash
hca respond <run-id> <question-id> "the answer"
```

Cancellation is deliberate and ownership-scoped:

```bash
hca stop <run-id>       # interactive confirmation
hca stop --yes <run-id> # automation
```

## Human and Hermes-agent surfaces

Both surfaces call the same versioned `FleetService` and result schemas.

| Human CLI | Hermes plugin tool | Purpose |
|---|---|---|
| `hca run` | `hca_team_run` | Start or resume one bounded mission |
| `hca run-status` | `hca_team_status` | Read state, questions, and ownership |
| `hca respond` | `hca_team_respond` | Answer one recorded question |
| `hca collect` | `hca_team_collect` | Produce the deterministic result manifest |
| `hca stop` | `hca_team_stop` | Approval-gated cancellation |

Lower-level fleet and Kanban commands remain operational diagnostics; they are
not the primary product workflow. Start with [Running a team](docs/running-a-team.md).

## GB10 optimization

HCA can consume GB10 memory pressure and optional engine metrics to make
conservative admission decisions. vLLM and SGLang remain external serving
infrastructure configured by the operator and Hermes profile.

```bash
hca init --preset gb10-vllm --model <served-model-id> --source-profile default
# or: --preset gb10-sglang
hca doctor
hca run --source-profile default "Verify this bounded single-host task"
```

No universal worker count is published. Measure each exact device,
model/runtime, endpoint, profile, context, and workload. Unknown telemetry never
means unlimited capacity.

## Safety boundaries

- HCA owns only its persisted task graph, exact worker identities, process
  groups, leases, tmux sessions, and HCA-created workspaces.
- Workers cannot expand or dispatch work outside the persisted HCA graph.
- Completion cannot be reported while a required exact worker remains live.
- Generated worker profiles use role-scoped tools and preserve the source
  profile's approval policy.
- Secrets and connection strings are not copied into HCA source, snapshots,
  state, logs, artifacts, or summaries.
- Remote agent placement and distributed Kanban replication are intentionally
  out of scope.

See [Security](docs/security.md), [Migration](docs/migration.md), and
[Operations](docs/operations.md).

## Documentation

- [Running a team](docs/running-a-team.md)
- [Architecture](docs/architecture.md)
- [Support matrix](docs/support-matrix.md)
- [Upstream compatibility](docs/upstream-compatibility.md)
- [Operations and recovery](docs/operations.md)
- [Migration, schema compatibility, and uninstall](docs/migration.md)
- [Security](docs/security.md)
- [GB10 optimization](docs/backends-vllm-sglang.md)
- [Remote-placement boundary](docs/gb10-cluster.md)
- [Benchmarking](docs/benchmarking.md)

## Attribution

HCA is an independent r0b0tlab project built on public interfaces and
behavior from [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
GB10 deployment references credit
[NVIDIA DGX Spark playbooks](https://github.com/NVIDIA/dgx-spark-playbooks).
Neither Nous Research nor NVIDIA endorses or maintains this repository. See
[`NOTICE`](NOTICE) for dependency and documentation attribution.

## License

MIT — see [LICENSE](LICENSE).
