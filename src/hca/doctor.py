"""Doctor checks for single-node and cluster (SSH) fleets."""

from __future__ import annotations

import getpass
import os
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any

from hca.backends import openai_compat as oai
from hca.config import FleetConfig
from hca.hermes_compat import (
    HermesCompatError,
    assert_dispatch_contract,
    compatibility_report,
    hermes_version,
)
from hca.resources import fetch_capacity
from hca.ssh_exec import run_ssh
from hca.tmux import TmuxManager


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    severity: str = "error"  # error|warn|info

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DoctorReport:
    ok: bool
    checks: list[Check] = field(default_factory=list)
    compat: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
            "compat": self.compat,
        }


def _add(checks: list[Check], name: str, ok: bool, detail: str, severity: str = "error") -> None:
    checks.append(Check(name=name, ok=ok, detail=detail, severity=severity if ok else severity))


def run_doctor(cfg: FleetConfig, *, tools_probe: bool = False) -> DoctorReport:
    checks: list[Check] = []

    # hermes
    try:
        ver = hermes_version()
        _add(checks, "hermes.version", True, ver, "info")
    except Exception as exc:
        _add(checks, "hermes.version", False, str(exc))

    try:
        info = assert_dispatch_contract()
        _add(
            checks,
            "hermes.dispatch_once",
            True,
            f"spawn_fn ok @ {info.path} sig={info.dispatch_signature}",
            "info",
        )
    except HermesCompatError as exc:
        _add(checks, "hermes.dispatch_once", False, str(exc))

    # Normalized compatibility report: lane classification + sole-dispatcher
    # ownership. Surfaced under `compat` in `hca doctor --json`. The gateway
    # probe here uses the real live-gateway seam, so a foreign dispatcher on
    # our board fails closed before any task is created.
    compat: dict[str, Any] = {}
    try:
        compat = compatibility_report(cfg.board)
    except Exception as exc:  # never let the report crash doctor
        compat = {"lane": "unknown", "lane_reason": f"report failed: {exc}"}
    lane = compat.get("lane", "unknown")
    if lane == "unsupported":
        _add(checks, "hermes.lane", False, compat.get("lane_reason", "unsupported"))
    elif lane == "edge":
        _add(checks, "hermes.lane", True, f"edge: {compat.get('lane_reason', '')}", "warn")
    elif lane == "stable":
        _add(checks, "hermes.lane", True, compat.get("lane_reason", "stable"), "info")
    else:
        _add(checks, "hermes.lane", False, compat.get("lane_reason", "unknown lane"))

    own = compat.get("dispatcher_ownership") or {}
    if own:
        if own.get("conflict"):
            _add(checks, "hermes.sole_dispatcher", False, own.get("reason", "dispatcher conflict"))
        else:
            _add(
                checks,
                "hermes.sole_dispatcher",
                True,
                own.get("reason", "HCA is sole dispatcher"),
                "info",
            )

    # tmux
    tmux = shutil.which("tmux")
    if tmux:
        _add(checks, "tmux.binary", True, tmux, "info")
        try:
            tm = TmuxManager(cfg.tmux_socket)
            tm.ensure_server()
            _add(checks, "tmux.server", True, f"socket={cfg.tmux_socket}", "info")
        except Exception as exc:
            _add(checks, "tmux.server", False, str(exc))
    else:
        _add(checks, "tmux.binary", False, "tmux not found on PATH")

    # endpoint local-only
    if cfg.backend.local_only and not oai.is_local_endpoint(cfg.backend.endpoint):
        _add(
            checks,
            "backend.local_only",
            False,
            f"endpoint {cfg.backend.endpoint} is not local/private; pass allow_remote or disable local_only",
        )
    else:
        _add(checks, "backend.local_only", True, f"endpoint={cfg.backend.endpoint}", "info")

    # engine + model
    _add(checks, "backend.engine", True, cfg.backend.engine.value, "info")
    if not cfg.backend.model:
        _add(checks, "backend.model", False, "model is empty — set --model or preset model")
    else:
        pr = oai.probe_models(cfg.backend.endpoint, cfg.backend.model)
        _add(checks, "backend.models", pr.ok, pr.detail)
        if pr.ok:
            ch = oai.probe_chat(cfg.backend.endpoint, cfg.backend.model)
            _add(checks, "backend.chat", ch.ok, ch.detail, severity="error" if not ch.ok else "info")
            if tools_probe:
                tp = oai.probe_tools(cfg.backend.endpoint, cfg.backend.model)
                _add(checks, "backend.tools", tp.ok, tp.detail, severity="warn")

    cap = fetch_capacity(cfg)
    _add(checks, "backend.capacity", cap.healthy, cap.detail, severity="warn" if not cap.healthy else "info")

    # Device adapter selection (capability-driven; generic imports no vendor libs)
    try:
        from hca.devices import probe_device

        dev, dev_reason = probe_device(disk_path=cfg.state_dir or None)
        compat["device"] = {"selected": dev.adapter, "reason": dev_reason, "signals": dev.to_dict()}
        _add(checks, "device.adapter", True, f"{dev.adapter} ({dev_reason}); {dev.detail}", "info")
    except Exception as exc:
        _add(checks, "device.adapter", True, f"device probe failed: {exc}", "warn")

    # state dir writable
    try:
        os.makedirs(cfg.state_dir, exist_ok=True)
        probe = os.path.join(cfg.state_dir, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        _add(checks, "state.dir", True, cfg.state_dir, "info")
    except Exception as exc:
        _add(checks, "state.dir", False, f"{cfg.state_dir}: {exc}")

    # cluster SSH
    if cfg.role.value in {"control", "node"} or cfg.cluster.nodes:
        me = getpass.getuser()
        for node in cfg.cluster.nodes:
            host = node.fabric_ip or node.host
            res = run_ssh(
                host,
                "whoami; command -v hermes; command -v tmux; hostname",
                user=node.ssh_user,
                port=node.ssh_port,
                connect_timeout=cfg.cluster.connect_timeout_seconds,
                timeout=cfg.cluster.command_timeout_seconds,
                batch_mode=cfg.cluster.ssh_batch_mode,
            )
            if not res.ok:
                _add(checks, f"cluster.ssh.{host}", False, res.stderr.strip() or res.stdout.strip() or "ssh failed")
                continue
            lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
            remote_user = lines[0] if lines else ""
            if cfg.cluster.require_same_username and remote_user and remote_user != me and not node.ssh_user:
                _add(
                    checks,
                    f"cluster.user.{host}",
                    False,
                    f"remote user {remote_user!r} != local {me!r} (NVIDIA require same username)",
                )
            else:
                _add(checks, f"cluster.ssh.{host}", True, " ".join(lines[:4]), "info")

    # UMA hint
    if os.path.exists("/proc/meminfo"):
        _add(
            checks,
            "uma.note",
            True,
            "Linux meminfo present; on DGX Spark prefer admission over automatic drop_caches",
            "info",
        )

    fatal = [c for c in checks if not c.ok and c.severity == "error"]
    return DoctorReport(ok=not fatal, checks=checks, compat=compat)
