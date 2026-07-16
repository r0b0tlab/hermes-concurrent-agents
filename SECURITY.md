# Security policy

## Supported code

This project is alpha. Security fixes target the current `main` branch and the
latest explicitly published release, if one exists. Historical `1.x` scaffolding
and unsupported remote agent-placement paths are not maintained security lanes.

## Reporting a vulnerability

Do not include credentials, private endpoint URLs, private hostnames, user data,
or exploit details in a public issue.

Use GitHub private vulnerability reporting for
`r0b0tlab/hermes-concurrent-agents` when it is available. Otherwise contact the
maintainer privately through the contact method listed on the r0b0tlab GitHub
organization profile. Include only the minimum sanitized reproduction needed to
understand the boundary.

A useful report contains:

- affected HCA commit/version and Hermes version;
- operating system and Python version;
- whether the issue crosses graph, profile, process, approval, credential, or
  result-integrity boundaries;
- sanitized reproduction steps;
- expected versus observed ownership; and
- whether any credential or private artifact may have been exposed.

Rotate any credential that may have entered logs or artifacts before sharing a
report.

## Scope

Especially relevant findings include:

- signaling or deleting a process/workspace not exactly owned by HCA;
- bypassing stop approval or source-profile approval policy;
- dispatching work outside the persisted HCA graph;
- leaking provider credentials or connection strings;
- reporting blocked/cancelled/unreviewed work as success; or
- bypassing the unsupported remote-placement guard.

HCA does not claim to sandbox malicious code running under the same Unix user.
That limitation alone is not a vulnerability unless HCA claims or enforces a
stronger boundary incorrectly.

See [docs/security.md](docs/security.md) for the complete security model.
