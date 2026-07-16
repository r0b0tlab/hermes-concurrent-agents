# Deployment guide

## Support targets

| Platform | Status |
|---|---|
| Linux, Python 3.11–3.12 | Supported portable control plane |
| Single DGX Spark / GB10 | Optimized optional telemetry |
| macOS | Portable CI smoke; no device-acceptance claim |
| Remote model endpoint | Supported through Hermes profile configuration |
| Remote agent placement | Unsupported |

The generated [support matrix](support-matrix.md) is authoritative.

## Install

```bash
git clone https://github.com/r0b0tlab/hermes-concurrent-agents.git
cd hermes-concurrent-agents
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Install and configure Hermes Agent separately. The required stable contract lane
is Hermes `0.18.2 / 2026.7.7.2`; `tmux` must be on `PATH`.

## Initialize from an existing Hermes profile

```bash
hca init \
  --preset generic-linux \
  --model <served-model-id> \
  --source-profile default
hca doctor
```

Single-host presets are `generic-linux`, `gb10-vllm`, and `gb10-sglang`.
The GB10 presets add telemetry/admission defaults; they do not provision an
engine. Cluster-placement presets are not part of the stable package surface.

Generated slot profiles live under the active Hermes home and are created with
`hermes profile create`. HCA preserves provider/model configuration through
Hermes, filters worker tools, disables worker plugins/delegation, preserves the
source approval policy, writes owner-only files, and validates each profile.

## State

The default state root is `~/.hca/<fleet>/` and may be overridden explicitly.
It contains:

- `hca.sqlite` — HCA ownership, leases, run projections, questions, and events;
- `fleet.resolved.json` — a credential-free resolved control snapshot;
- `controllers/` — private controller identity/config snapshots;
- `logs/` — board/task/run-namespaced worker output;
- `worktrees/` — HCA-created workspaces; and
- `DRAIN` — admission stop flag when present.

Hermes Kanban remains task truth; HCA does not replicate editable task status.
See [Migration](migration.md) before moving or restoring state.

Package-preset endpoints are reconstructed from installed preset data. A custom
endpoint or metrics URL is never written to `fleet.resolved.json`; supply it to
each process through `--config`, `--endpoint`, `HCA_BACKEND_ENDPOINT`, and (when
needed) `HCA_BACKEND_METRICS_URL` / `HCA_AUXILIARY_ENDPOINT`.

## Endpoint and credential boundary

vLLM, SGLang, hosted APIs, and custom OpenAI-compatible endpoints remain
operator/Hermes infrastructure. HCA reads only optional health/admission signals.
It does not copy credentials, normalize providers, launch servers, or choose
fallback endpoints.

Bind local serving endpoints to trusted interfaces and networks. Configure
remote endpoints and authentication through Hermes profiles, not HCA fleet
files.

## Verification

```bash
hca doctor
hca run --source-profile default "Return one bounded verified result"
hca collect <run-id>
```

Before a public release, run `scripts/release-check.sh --full` and the clean-
wheel/plugin discovery gate. See [Security](security.md) for the ownership model.

## Uninstall

Package uninstall preserves state, results, worktrees, profiles, and credentials.
Follow [Migration and uninstall](migration.md) before deleting any user data.
