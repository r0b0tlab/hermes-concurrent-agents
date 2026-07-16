# Remote placement and GB10 clusters

## Status

**Remote agent placement is unsupported in the stable HCA surface.** HCA may
control multiple workers on one host, and those workers may use a model endpoint
hosted elsewhere through their existing Hermes profiles. HCA does not place or
supervise workers on remote hosts.

The distinction is load-bearing:

- **Remote inference:** supported. The Hermes profile owns provider selection,
  endpoint configuration, authentication, and fallback.
- **Remote agent placement:** unsupported. The authoritative Hermes Kanban
  SQLite board, claims, heartbeats, comments, task completion, and HCA process
  ownership cannot safely cross hosts with the available local transport.

## Fail-closed behavior

The old `hca cluster nodes up` compatibility command exits with preflight code
`3` before opening SSH. Likewise, starting or initializing a fleet with legacy
`control` or `node` roles fails before profiles, state, or supervisors are
created. The shared `FleetService` applies the same rule to Hermes plugin calls.

Two read-only/local helpers remain explicitly experimental:

- `hca cluster nodes add HOST...` records local inventory only.
- `hca cluster doctor` reports SSH reachability and local capability checks; it
  does **not** prove task placement, lifecycle ownership, or recovery.

## Why HCA does not invent a transport

Hermes Kanban is the source of task truth. A correct remote worker transport
must preserve atomic claim ownership, exact run identity, heartbeat/comment/
completion semantics, stale-worker recovery, and board authorization. HCA will
not work around that boundary with:

- NFS-mounted SQLite;
- ad hoc SQLite replication;
- an HCA-owned distributed task database;
- remote shell success treated as application success; or
- modifications to NousResearch repositories.

A future placement design can be considered only when HCA can consume an
existing supported Hermes remote Kanban transport and pass capacity-aware
placement, node-loss recovery, duplicate-worker, and cleanup acceptance.

## Supported multi-node topology

It is safe to run the model server on another admitted node and keep the agents,
Kanban board, HCA controller, state, and workspaces together on the control host:

```text
single HCA/Kanban/worker host ── ordinary Hermes profile ── remote model endpoint
```

Use the selected Hermes profile to configure that endpoint. Do not copy its
credential or connection string into HCA fleet files, state, logs, or result
artifacts.

NVIDIA's connect-two/connect-three/switch playbooks remain useful for hardware
and inference networking, but completing those playbooks does not enable HCA
remote agent placement.
