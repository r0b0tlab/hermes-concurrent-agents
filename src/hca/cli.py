"""hca CLI — fleet control plane entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from hca import __version__
from hca.bench import run_bench
from hca.config import list_presets, load_fleet_config
from hca.doctor import run_doctor
from hca.hermes_compat import HermesCompatError, hermes_bin, hermes_version, run_hermes
from hca.logs import follow_log, read_log
from hca.observe import format_status_table, peek_slot, status_rows
from hca.profiles import init_profiles
from hca.resources import fetch_capacity
from hca.ssh_exec import run_ssh
from hca.state import StateDB
from hca.supervisor import Supervisor
from hca.tmux import TmuxManager, sanitize_session_name
from hca.transcript import fetch_transcript, resolve_run
from hca.workspaces import ensure_worktree, mode_for_role


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


def _state(cfg) -> StateDB:
    Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
    return StateDB(Path(cfg.state_dir) / "hca.sqlite")


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
    snap = Path(cfg.state_dir) / "fleet.resolved.json"
    if not args.dry_run:
        snap.write_text(json.dumps(cfg.to_dict(), indent=2), encoding="utf-8")
        StateDB(Path(cfg.state_dir) / "hca.sqlite")
        (Path(cfg.state_dir) / "logs").mkdir(exist_ok=True)
        (Path(cfg.state_dir) / "worktrees").mkdir(exist_ok=True)
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
        print(
            f"supervisor running fleet={cfg.name} socket={cfg.tmux_socket} (Ctrl-C to stop)"
        )
        sup.run_forever()
        return 0
    report = sup.tick(dispatch=not args.no_dispatch)
    _print(report, args.json)
    return 0 if report.get("ok") else 1


def cmd_drain(args) -> int:
    cfg = _cfg_from_args(args)
    flag = Path(cfg.state_dir) / "DRAIN"
    if args.clear:
        if flag.exists():
            flag.unlink()
        msg = "drain cleared — admitting new work"
    else:
        Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
        flag.write_text(str(time.time()), encoding="utf-8")
        msg = "drain set — stop admitting new work; active runs continue"
    state = _state(cfg)
    state.set_activity(kind="fleet.drain", message=msg)
    _print({"ok": True, "drain": not args.clear, "message": msg}, args.json)
    if not args.json:
        print(msg)
    return 0


def cmd_down(args) -> int:
    cfg = _cfg_from_args(args)
    state = _state(cfg)
    # set drain first
    Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
    (Path(cfg.state_dir) / "DRAIN").write_text(str(time.time()), encoding="utf-8")
    tmux = TmuxManager(cfg.tmux_socket)
    killed = []
    if args.kill:
        for rec in state.list_runs(status="running"):
            tmux.signal_pane(rec.tmux_session, "TERM")
            state.mark_run_status(rec.board, rec.run_id, "stopped", error="hca down --kill")
            killed.append(rec.run_id)
        if args.slots:
            for s in tmux.list_sessions():
                if s.startswith(f"hca-{cfg.name}-"):
                    tmux.kill_session(s)
                    killed.append(s)
    state.set_activity(
        kind="fleet.down",
        message=f"down kill={args.kill} slots={args.slots} killed={len(killed)}",
    )
    out = {
        "ok": True,
        "drained": True,
        "kill": args.kill,
        "slots": args.slots,
        "affected": killed,
    }
    _print(out, args.json)
    if not args.json:
        print(
            "fleet down (drain set)"
            + ("; signaled running panes" if args.kill else "; slots preserved")
        )
    return 0


def cmd_ps(args) -> int:
    cfg = _cfg_from_args(args)
    state = _state(cfg)
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
    state = _state(cfg)
    tmux = TmuxManager(cfg.tmux_socket)
    interval = args.interval or cfg.observe.watch_interval_seconds
    try:
        while True:
            if not args.json:
                os.system("clear" if os.name != "nt" else "cls")
                drain = (Path(cfg.state_dir) / "DRAIN").exists()
                print(
                    f"hca watch fleet={cfg.name} engine={cfg.backend.engine.value}"
                    f" drain={'ON' if drain else 'off'}  {time.strftime('%H:%M:%S')}"
                )
                print(format_status_table(status_rows(cfg, state, tmux)))
                cap = fetch_capacity(cfg)
                print(
                    f"\ncapacity: healthy={cap.healthy} kv={cap.kv_cache_util}"
                    f" running={cap.active_sequences} waiting={cap.waiting} ({cap.detail})"
                )
            else:
                print(
                    json.dumps(
                        {
                            "rows": status_rows(cfg, state, tmux),
                            "capacity": fetch_capacity(cfg).to_dict(),
                            "drain": (Path(cfg.state_dir) / "DRAIN").exists(),
                        },
                        default=str,
                    )
                )
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def cmd_peek(args) -> int:
    cfg = _cfg_from_args(args)
    tmux = TmuxManager(cfg.tmux_socket)
    state = _state(cfg)
    target = args.target
    rec = resolve_run(state, target)
    if rec:
        target = rec["tmux_session"]
    try:
        text = peek_slot(cfg, tmux, target)
    except Exception as exc:
        print(f"peek failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print({"target": args.target, "session": target, "text": text}, True)
    else:
        print(text)
    return 0


def cmd_attach(args) -> int:
    cfg = _cfg_from_args(args)
    state = _state(cfg)
    tmux = TmuxManager(cfg.tmux_socket)
    target = args.target
    rec = resolve_run(state, target)
    if rec:
        target = rec["tmux_session"]
    name = sanitize_session_name(target)
    if not tmux.has_session(name):
        print(f"no tmux session {name}", file=sys.stderr)
        return 1
    cmd = tmux.attach_command(name)
    # Replace process so terminal is usable
    os.execvp(cmd[0], cmd)
    return 0


def cmd_logs(args) -> int:
    cfg = _cfg_from_args(args)
    state = _state(cfg)
    rec = resolve_run(state, args.target)
    run_id = rec["run_id"] if rec else args.target
    if args.follow:
        try:
            for line in follow_log(cfg.state_dir, run_id):
                print(line, flush=True)
        except KeyboardInterrupt:
            return 0
        return 0
    text = read_log(cfg.state_dir, run_id, tail=args.tail)
    if not text and rec:
        # fallback to peek
        tmux = TmuxManager(cfg.tmux_socket)
        try:
            text = peek_slot(cfg, tmux, rec["tmux_session"])
        except Exception:
            text = ""
    if args.json:
        _print({"run_id": run_id, "text": text}, True)
    else:
        print(text or f"(no log for {run_id})")
    return 0


def cmd_activity(args) -> int:
    cfg = _cfg_from_args(args)
    state = _state(cfg)
    rows = state.recent_activity(limit=args.limit)
    if args.follow:
        seen = {r["id"] for r in rows}
        if args.json:
            print(json.dumps(rows, default=str))
        else:
            for r in reversed(rows):
                print(f"{r['ts']:.0f} {r['kind']} {r.get('message', '')}")
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
                        print(
                            f"{r['ts']:.0f} {r['kind']} {r.get('message', '')}",
                            flush=True,
                        )
        except KeyboardInterrupt:
            return 0
    if args.json:
        _print(rows, True)
    else:
        for r in reversed(rows):
            print(f"{r['ts']:.0f} {r['kind']} {r.get('message', '')}")
    return 0


def cmd_transcript(args) -> int:
    cfg = _cfg_from_args(args)
    state = _state(cfg)
    data = fetch_transcript(
        state,
        args.target,
        limit=args.limit,
        redact_patterns=cfg.observe.redact_patterns,
    )
    if args.json:
        _print(data, True)
        return 0 if not data.get("error") or data.get("messages") else 1
    if data.get("run"):
        r = data["run"]
        print(
            f"run={r.get('run_id')} task={r.get('task_id')} slot={r.get('slot')} "
            f"status={r.get('status')} source={data.get('source')}"
        )
    for m in data.get("messages") or []:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        print(f"\n[{role}]\n{content}")
    if data.get("error") and not data.get("messages"):
        print(f"(transcript unavailable: {data['error']})", file=sys.stderr)
        return 1
    return 0


def cmd_inspect(args) -> int:
    cfg = _cfg_from_args(args)
    state = _state(cfg)
    rec = resolve_run(state, args.target)
    if not rec:
        print(f"no run for {args.target}", file=sys.stderr)
        return 1
    acts = [
        a
        for a in state.recent_activity(100)
        if a.get("run_id") == rec.get("run_id") or a.get("task_id") == rec.get("task_id")
    ]
    out = {"run": rec, "activity": acts[:20], "capacity": fetch_capacity(cfg).to_dict()}
    _print(out, True if args.json else False)
    if not args.json:
        print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_explain(args) -> int:
    cfg = _cfg_from_args(args)
    sup = Supervisor(cfg)
    decision = sup.can_admit()
    state = _state(cfg)
    hits = [
        a
        for a in state.recent_activity(200)
        if args.target in json.dumps(a, default=str)
    ]
    drain = (Path(cfg.state_dir) / "DRAIN").exists()
    out = {
        "target": args.target,
        "drain": drain,
        "admission": decision,
        "recent": hits[:10],
    }
    _print(out, args.json)
    return 0


def cmd_dashboard(args) -> int:
    cfg = _cfg_from_args(args)
    # Hermes Kanban dashboard — do not build a second web UI
    url = args.url or "http://127.0.0.1:9119/"
    board = cfg.board
    print(f"Open Hermes dashboard: {url}")
    print(f"Board: {board}")
    print("HCA does not ship a second chat UI — use hermes dashboard / kanban.")
    if args.open:
        try:
            subprocess.Popen(
                ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
            )
        except Exception as exc:
            print(f"open failed: {exc}", file=sys.stderr)
            return 1
    return 0


def cmd_task(args) -> int:
    cfg = _cfg_from_args(args)
    # thin wrapper over hermes kanban — durable truth stays in Hermes
    if args.task_cmd == "add":
        title = args.title
        cmd = [
            "kanban",
            "create",
            title,
            "--board",
            cfg.board,
        ]
        if args.assignee:
            cmd += ["--assignee", args.assignee]
        if args.goal:
            cmd += ["--goal"] if _supports_goal() else []
        # workspace prep
        if args.repo:
            role = getattr(args, "task_role", None) or "coder"
            # create placeholder worktree path for later spawn
            ws = ensure_worktree(
                repo=args.repo,
                task_id=f"pending-{int(time.time())}",
                role=role,
                state_dir=cfg.state_dir,
                fleet=cfg.name,
                mode=mode_for_role(role),
            )
            if not args.json:
                print(f"workspace prepared: {ws.path} mode={ws.mode}")
        proc = run_hermes(*cmd)
        if args.json:
            _print({"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}, True)
        else:
            sys.stdout.write(proc.stdout)
            sys.stderr.write(proc.stderr)
        return proc.returncode
    if args.task_cmd == "swarm":
        cmd = ["kanban", "swarm", args.title, "--board", cfg.board]
        if args.workers:
            # hermes swarm flags vary; pass through generically if present
            pass
        proc = run_hermes(*cmd)
        if args.json:
            _print({"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}, True)
        else:
            sys.stdout.write(proc.stdout)
            sys.stderr.write(proc.stderr)
        return proc.returncode
    if args.task_cmd == "list":
        proc = run_hermes("kanban", "list", "--board", cfg.board)
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return proc.returncode
    if args.task_cmd == "show":
        proc = run_hermes("kanban", "show", args.task_id, "--board", cfg.board)
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return proc.returncode
    if args.task_cmd == "comment":
        proc = run_hermes(
            "kanban", "comment", args.task_id, args.text, "--board", cfg.board
        )
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return proc.returncode
    print("usage: hca task add|swarm|list|show|comment", file=sys.stderr)
    return 2


def _supports_goal() -> bool:
    try:
        proc = run_hermes("kanban", "create", "--help")
        return "--goal" in (proc.stdout + proc.stderr)
    except Exception:
        return False


def cmd_plan(args) -> int:
    cfg = _cfg_from_args(args)
    slots = sum(int(v) for v in cfg.profile_slots.values())
    out = {
        "fleet": cfg.name,
        "board": cfg.board,
        "engine": cfg.backend.engine.value,
        "endpoint": cfg.backend.endpoint,
        "slots": slots,
        "max_top_level_runs": cfg.capacity.max_top_level_runs,
        "max_total_sequences": cfg.capacity.max_total_sequences,
        "max_wave_size": cfg.capacity.max_wave_size,
        "dry_run": True,
        "estimate": (
            f"Can admit ~{cfg.capacity.max_top_level_runs} top-level runs and "
            f"{cfg.capacity.max_total_sequences} sequence credits "
            f"(wave≤{cfg.capacity.max_wave_size}). Measure knee with hca bench."
        ),
    }
    _print(out, True if args.json else False)
    if not args.json:
        print(json.dumps(out, indent=2))
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
            if (
                c.name.startswith("cluster.")
                or c.name.startswith("hermes.")
                or c.name.startswith("backend.")
            ):
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
        remote = "hca up --role node || true"
        res = run_ssh(host, remote, batch_mode=True, timeout=120)
        results.append(
            {
                "host": host,
                "ok": res.ok,
                "stdout": res.stdout[-500:],
                "stderr": res.stderr[-500:],
            }
        )
    _print(results, args.json)
    return 0 if all(r["ok"] for r in results) else 1


def cmd_bench(args) -> int:
    cfg = _cfg_from_args(args)
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    engine = args.engine or cfg.backend.engine.value
    endpoint = args.endpoint or cfg.backend.endpoint
    model = args.model or cfg.backend.model
    if not model and not args.dry_run:
        print("--model is required for live bench (or set via init/preset)", file=sys.stderr)
        return 2
    out_path = args.out or str(
        Path(cfg.state_dir) / "bench" / f"{engine}-{int(time.time())}.json"
    )
    report = run_bench(
        engine=engine,
        endpoint=endpoint,
        model=model or "unknown",
        levels=levels,
        requests_per_worker=args.requests_per_worker,
        dry_run=args.dry_run,
        out_path=None if args.dry_run else out_path,
    )
    _print(report.to_dict(), True)
    if not args.dry_run:
        print(f"wrote {out_path}", file=sys.stderr)
    return 0 if report.levels or args.dry_run else 1


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
        description="Hermes Concurrent Agents control plane (GB10 / Spark first)",
        parents=[common],
    )
    p.add_argument("--version", action="store_true")

    sp = p.add_subparsers(dest="cmd")

    sp.add_parser("version", parents=[common])
    sp.add_parser("presets", parents=[common])

    p_init = sp.add_parser(
        "init", parents=[common], help="Initialize fleet state + slot profiles"
    )
    p_init.add_argument("--force", action="store_true")
    p_init.add_argument("--dry-run", action="store_true")

    p_doc = sp.add_parser(
        "doctor", parents=[common], help="Validate Hermes/tmux/backend/cluster"
    )
    p_doc.add_argument("--tools", action="store_true", help="probe tool-calling")

    p_up = sp.add_parser(
        "up", parents=[common], help="Warm slots + reconcile (+ dispatch tick)"
    )
    p_up.add_argument("--daemon", action="store_true")
    p_up.add_argument(
        "--no-dispatch",
        action="store_true",
        help="warm/reconcile only (skip Kanban dispatch)",
    )

    p_drain = sp.add_parser("drain", parents=[common], help="Stop admitting new work")
    p_drain.add_argument("--clear", action="store_true", help="clear drain flag")

    p_down = sp.add_parser("down", parents=[common], help="Drain fleet; optional kill")
    p_down.add_argument("--kill", action="store_true", help="signal running worker panes")
    p_down.add_argument(
        "--slots", action="store_true", help="with --kill, destroy warm tmux slots"
    )

    sp.add_parser("ps", parents=[common], help="Physical slot/process view")
    sp.add_parser("status", parents=[common], help="Alias of ps")

    p_watch = sp.add_parser("watch", parents=[common], help="Live mission-control table")
    p_watch.add_argument("--interval", type=float, default=0.0)

    p_peek = sp.add_parser("peek", parents=[common], help="Read-only tmux pane snapshot")
    p_peek.add_argument("target")

    p_attach = sp.add_parser(
        "attach", parents=[common], help="Interactive tmux attach (intrusive)"
    )
    p_attach.add_argument("target")

    p_logs = sp.add_parser("logs", parents=[common], help="Run logs / pane fallback")
    p_logs.add_argument("target")
    p_logs.add_argument("--follow", action="store_true")
    p_logs.add_argument("--tail", type=int, default=200)

    p_act = sp.add_parser("activity", parents=[common], help="Lifecycle/activity stream")
    p_act.add_argument("--follow", action="store_true")
    p_act.add_argument("--limit", type=int, default=50)

    p_tr = sp.add_parser(
        "transcript", parents=[common], help="Conversation / activity transcript"
    )
    p_tr.add_argument("target")
    p_tr.add_argument("--limit", type=int, default=100)

    p_ins = sp.add_parser("inspect", parents=[common], help="Full run mapping dump")
    p_ins.add_argument("target")

    p_exp = sp.add_parser(
        "explain", parents=[common], help="Why waiting / admission decision"
    )
    p_exp.add_argument("target")

    p_dash = sp.add_parser(
        "dashboard", parents=[common], help="Point at Hermes dashboard (no second UI)"
    )
    p_dash.add_argument("--url", default="http://127.0.0.1:9119/")
    p_dash.add_argument("--open", action="store_true")

    p_plan = sp.add_parser(
        "plan", parents=[common], help="Capacity dry-run estimate for this fleet"
    )

    p_task = sp.add_parser("task", parents=[common], help="Kanban task helpers")
    t_sp = p_task.add_subparsers(dest="task_cmd")
    p_add = t_sp.add_parser("add", parents=[common])
    p_add.add_argument("title")
    p_add.add_argument("--assignee", default="")
    p_add.add_argument("--task-role", default="coder", dest="task_role")
    p_add.add_argument("--repo", default="")
    p_add.add_argument("--goal", action="store_true")
    p_sw = t_sp.add_parser("swarm", parents=[common])
    p_sw.add_argument("title")
    p_sw.add_argument("--workers", default="")
    t_sp.add_parser("list", parents=[common])
    p_show = t_sp.add_parser("show", parents=[common])
    p_show.add_argument("task_id")
    p_com = t_sp.add_parser("comment", parents=[common])
    p_com.add_argument("task_id")
    p_com.add_argument("text")

    p_cl = sp.add_parser("cluster", parents=[common], help="Cluster inventory/doctor/up")
    cl_sp = p_cl.add_subparsers(dest="cluster_cmd")
    p_nodes = cl_sp.add_parser("nodes", parents=[common])
    n_sp = p_nodes.add_subparsers(dest="nodes_cmd")
    p_nodes_add = n_sp.add_parser("add", parents=[common])
    p_nodes_add.add_argument("hosts", nargs="+")
    n_sp.add_parser("up", parents=[common])
    cl_sp.add_parser("doctor", parents=[common])

    p_bench = sp.add_parser(
        "bench", parents=[common], help="Concurrency benchmark (vLLM/SGLang)"
    )
    p_bench.add_argument("--levels", default="1,2,3,4,6,8")
    p_bench.add_argument("--requests-per-worker", type=int, default=3)
    p_bench.add_argument("--dry-run", action="store_true")
    p_bench.add_argument("--out", default="")

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

    dispatch = {
        "version": cmd_version,
        "presets": cmd_presets,
        "init": cmd_init,
        "doctor": cmd_doctor,
        "up": cmd_up,
        "drain": cmd_drain,
        "down": cmd_down,
        "ps": cmd_ps,
        "status": cmd_status,
        "watch": cmd_watch,
        "peek": cmd_peek,
        "attach": cmd_attach,
        "logs": cmd_logs,
        "activity": cmd_activity,
        "transcript": cmd_transcript,
        "inspect": cmd_inspect,
        "explain": cmd_explain,
        "dashboard": cmd_dashboard,
        "plan": cmd_plan,
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
    if args.cmd == "task":
        return cmd_task(args)

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
