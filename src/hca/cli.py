"""hca CLI — fleet control plane entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from hca import __version__
from hca.config import list_presets, load_fleet_config
from hca.doctor import run_doctor
from hca.hermes_compat import HermesCompatError, assert_dispatch_contract, hermes_version
from hca.observe import format_status_table, peek_slot, status_rows
from hca.profiles import init_profiles
from hca.resources import fetch_capacity
from hca.state import StateDB
from hca.supervisor import Supervisor
from hca.tmux import TmuxManager
from hca.ssh_exec import run_ssh


def _print(data: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        if isinstance(data, dict) and "text" in data:
            print(data["text"])
        else:
            print(data)


def _cfg_from_args(args: argparse.Namespace):
    return load_fleet_config(
        preset=getattr(args, "preset", "") or "",
        config_path=getattr(args, "config", "") or "",
        endpoint=getattr(args, "endpoint", "") or "",
        model=getattr(args, "model", "") or "",
        engine=getattr(args, "engine", "") or "",
        board=getattr(args, "board", "") or "",
        role=getattr(args, "role", "") or "",
        state_dir=getattr(args, "state_dir", "") or "",
    )


def cmd_version(_args) -> int:
    print(f"hca {__version__}")
    try:
        print(hermes_version())
    except Exception as exc:
        print(f"hermes: unavailable ({exc})", file=sys.stderr)
    return 0


def cmd_presets(_args) -> int:
    for p in list_presets():
        print(p)
    return 0


def cmd_init(args) -> int:
    cfg = _cfg_from_args(args)
    Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
    # persist a resolved config snapshot
    snap = Path(cfg.state_dir) / "fleet.resolved.json"
    if not args.dry_run:
        snap.write_text(json.dumps(cfg.to_dict(), indent=2), encoding="utf-8")
        StateDB(Path(cfg.state_dir) / "hca.sqlite")
    profiles = init_profiles(cfg, force=args.force, dry_run=args.dry_run)
    out = {
        "preset": cfg.preset,
        "state_dir": cfg.state_dir,
        "board": cfg.board,
        "backend": cfg.backend.endpoint,
        "model": cfg.backend.model,
        "engine": cfg.backend.engine.value,
        "profiles": profiles,
        "dry_run": args.dry_run,
    }
    _print(out, args.json)
    if not args.json:
        print(f"init complete → {cfg.state_dir}")
        print("next: hca doctor && hca up")
    return 0


def cmd_doctor(args) -> int:
    cfg = _cfg_from_args(args)
    report = run_doctor(cfg, tools_probe=args.tools)
    if args.json:
        _print(report.to_dict(), True)
    else:
        for c in report.checks:
            mark = "ok" if c.ok else c.severity.upper()
            print(f"[{mark}] {c.name}: {c.detail}")
        print("PASS" if report.ok else "FAIL")
    return 0 if report.ok else 1


def cmd_up(args) -> int:
    cfg = _cfg_from_args(args)
    if args.role:
        from hca.models import FleetRole

        cfg.role = FleetRole(args.role)
    sup = Supervisor(cfg)
    if args.daemon:
        print(f"supervisor running fleet={cfg.name} socket={cfg.tmux_socket} (Ctrl-C to stop)")
        sup.run_forever()
        return 0
    report = sup.tick()
    _print(report, args.json)
    return 0 if report.get("ok") else 1


def cmd_ps(args) -> int:
    cfg = _cfg_from_args(args)
    state = StateDB(Path(cfg.state_dir) / "hca.sqlite")
    tmux = TmuxManager(cfg.tmux_socket)
    rows = status_rows(cfg, state, tmux)
    if args.json:
        _print(rows, True)
    else:
        print(format_status_table(rows))
    return 0


def cmd_status(args) -> int:
    return cmd_ps(args)


def cmd_watch(args) -> int:
    cfg = _cfg_from_args(args)
    state = StateDB(Path(cfg.state_dir) / "hca.sqlite")
    tmux = TmuxManager(cfg.tmux_socket)
    interval = args.interval or cfg.observe.watch_interval_seconds
    try:
        while True:
            if not args.json:
                os.system("clear" if os.name != "nt" else "cls")
                print(f"hca watch fleet={cfg.name} engine={cfg.backend.engine.value}  {time.strftime('%H:%M:%S')}")
                print(format_status_table(status_rows(cfg, state, tmux)))
                cap = fetch_capacity(cfg)
                print(f"\ncapacity: healthy={cap.healthy} kv={cap.kv_cache_util} running={cap.active_sequences} waiting={cap.waiting} ({cap.detail})")
            else:
                print(json.dumps({"rows": status_rows(cfg, state, tmux), "capacity": fetch_capacity(cfg).to_dict()}, default=str))
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def cmd_peek(args) -> int:
    cfg = _cfg_from_args(args)
    tmux = TmuxManager(cfg.tmux_socket)
    try:
        text = peek_slot(cfg, tmux, args.target)
    except Exception as exc:
        print(f"peek failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print({"target": args.target, "text": text}, True)
    else:
        print(text)
    return 0


def cmd_activity(args) -> int:
    cfg = _cfg_from_args(args)
    state = StateDB(Path(cfg.state_dir) / "hca.sqlite")
    rows = state.recent_activity(limit=args.limit)
    if args.follow:
        seen = {r["id"] for r in rows}
        if args.json:
            print(json.dumps(rows, default=str))
        else:
            for r in reversed(rows):
                print(f"{r['ts']:.0f} {r['kind']} {r.get('message','')}")
        try:
            while True:
                time.sleep(1)
                for r in state.recent_activity(limit=50):
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    if args.json:
                        print(json.dumps(r, default=str), flush=True)
                    else:
                        print(f"{r['ts']:.0f} {r['kind']} {r.get('message','')}", flush=True)
        except KeyboardInterrupt:
            return 0
    _print(rows if args.json else {"text": "\n".join(f"{r['ts']:.0f} {r['kind']} {r.get('message','')}" for r in reversed(rows))}, args.json)
    return 0


def cmd_explain(args) -> int:
    cfg = _cfg_from_args(args)
    sup = Supervisor(cfg)
    decision = sup.can_admit()
    # also scan activity for target
    state = StateDB(Path(cfg.state_dir) / "hca.sqlite")
    hits = [a for a in state.recent_activity(200) if args.target in json.dumps(a)]
    out = {"target": args.target, "admission": decision, "recent": hits[:10]}
    _print(out, args.json)
    return 0


def cmd_cluster_nodes_add(args) -> int:
    cfg = _cfg_from_args(args)
    path = Path(cfg.state_dir) / "nodes.json"
    Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
    nodes = []
    if path.exists():
        nodes = json.loads(path.read_text(encoding="utf-8"))
    for h in args.hosts:
        if not any(n.get("host") == h for n in nodes):
            nodes.append({"host": h})
    path.write_text(json.dumps(nodes, indent=2), encoding="utf-8")
    _print({"nodes": nodes, "path": str(path)}, args.json)
    return 0


def cmd_cluster_doctor(args) -> int:
    cfg = _cfg_from_args(args)
    # merge nodes.json inventory
    path = Path(cfg.state_dir) / "nodes.json"
    if path.exists():
        from hca.models import ClusterNode

        for n in json.loads(path.read_text(encoding="utf-8")):
            cfg.cluster.nodes.append(ClusterNode(host=n["host"]))
    report = run_doctor(cfg)
    if args.json:
        _print(report.to_dict(), True)
    else:
        for c in report.checks:
            if c.name.startswith("cluster.") or c.name.startswith("hermes.") or c.name.startswith("backend."):
                mark = "ok" if c.ok else "FAIL"
                print(f"[{mark}] {c.name}: {c.detail}")
    return 0 if report.ok else 1


def cmd_cluster_nodes_up(args) -> int:
    cfg = _cfg_from_args(args)
    path = Path(cfg.state_dir) / "nodes.json"
    if not path.exists():
        print("no nodes.json — run: hca cluster nodes add HOST...", file=sys.stderr)
        return 1
    nodes = json.loads(path.read_text(encoding="utf-8"))
    results = []
    for n in nodes:
        host = n["host"]
        # idempotent remote up tick
        remote = "hca up --role node || true"
        res = run_ssh(host, remote, batch_mode=True, timeout=120)
        results.append({"host": host, "ok": res.ok, "stdout": res.stdout[-500:], "stderr": res.stderr[-500:]})
    _print(results, args.json)
    return 0 if all(r["ok"] for r in results) else 1


def cmd_bench(args) -> int:
    # dry-run analysis scaffold
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    out = {
        "engine": args.engine or "vllm",
        "levels": levels,
        "dry_run": True,
        "note": "full bench harness lands with Task 14; use scripts/benchmark.sh for raw endpoint sweeps today",
    }
    _print(out, True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON output where supported")
    common.add_argument("--preset", default="", help="preset name (e.g. gb10-vllm)")
    common.add_argument("--config", default="", help="TOML config path")
    common.add_argument("--endpoint", default="")
    common.add_argument("--model", default="")
    common.add_argument(
        "--engine", default="", choices=["", "vllm", "sglang", "openai_compat"]
    )
    common.add_argument("--board", default="")
    common.add_argument("--state-dir", default="")
    common.add_argument(
        "--role", default="", choices=["", "single", "control", "node"]
    )

    p = argparse.ArgumentParser(
        prog="hca",
        description="Hermes Concurrent Agents control plane",
        parents=[common],
    )
    p.add_argument("--version", action="store_true")

    sp = p.add_subparsers(dest="cmd")

    sp.add_parser("version", parents=[common])
    sp.add_parser("presets", parents=[common])

    p_init = sp.add_parser("init", parents=[common], help="Initialize fleet state + slot profiles")
    p_init.add_argument("--force", action="store_true")
    p_init.add_argument("--dry-run", action="store_true")

    p_doc = sp.add_parser("doctor", parents=[common], help="Validate Hermes/tmux/backend/cluster")
    p_doc.add_argument("--tools", action="store_true", help="probe tool-calling")

    p_up = sp.add_parser("up", parents=[common], help="Warm slots + reconcile (or run supervisor)")
    p_up.add_argument("--daemon", action="store_true")

    sp.add_parser("ps", parents=[common], help="Physical slot/process view")
    sp.add_parser("status", parents=[common], help="Alias of ps")

    p_watch = sp.add_parser("watch", parents=[common], help="Live mission-control table")
    p_watch.add_argument("--interval", type=float, default=0.0)

    p_peek = sp.add_parser("peek", parents=[common], help="Read-only tmux pane snapshot")
    p_peek.add_argument("target")

    p_act = sp.add_parser("activity", parents=[common], help="Lifecycle/activity stream")
    p_act.add_argument("--follow", action="store_true")
    p_act.add_argument("--limit", type=int, default=50)

    p_exp = sp.add_parser("explain", parents=[common], help="Why waiting / admission decision")
    p_exp.add_argument("target")

    p_cl = sp.add_parser("cluster", parents=[common], help="Cluster inventory/doctor/up")
    cl_sp = p_cl.add_subparsers(dest="cluster_cmd")
    p_nodes = cl_sp.add_parser("nodes", parents=[common])
    n_sp = p_nodes.add_subparsers(dest="nodes_cmd")
    p_nodes_add = n_sp.add_parser("add", parents=[common])
    p_nodes_add.add_argument("hosts", nargs="+")
    n_sp.add_parser("up", parents=[common])
    cl_sp.add_parser("doctor", parents=[common])

    p_bench = sp.add_parser("bench", parents=[common], help="Concurrency benchmark (scaffold)")
    p_bench.add_argument("--levels", default="1,2,3")
    p_bench.add_argument("--dry-run", action="store_true")

    return p


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version and not args.cmd:
        return cmd_version(args)
    if not args.cmd:
        parser.print_help()
        return 2

    # default state dir for commands that need fleet identity
    if not getattr(args, "state_dir", None):
        pass

    dispatch = {
        "version": cmd_version,
        "presets": cmd_presets,
        "init": cmd_init,
        "doctor": cmd_doctor,
        "up": cmd_up,
        "ps": cmd_ps,
        "status": cmd_status,
        "watch": cmd_watch,
        "peek": cmd_peek,
        "activity": cmd_activity,
        "explain": cmd_explain,
        "bench": cmd_bench,
    }
    if args.cmd == "cluster":
        if args.cluster_cmd == "doctor":
            return cmd_cluster_doctor(args)
        if args.cluster_cmd == "nodes":
            if args.nodes_cmd == "add":
                return cmd_cluster_nodes_add(args)
            if args.nodes_cmd == "up":
                return cmd_cluster_nodes_up(args)
        print("usage: hca cluster nodes add|up | hca cluster doctor", file=sys.stderr)
        return 2

    fn = dispatch.get(args.cmd)
    if not fn:
        parser.print_help()
        return 2
    try:
        return fn(args)
    except HermesCompatError as exc:
        print(f"compat error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        if os.environ.get("HCA_DEBUG"):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
