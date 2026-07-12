"""Fleet supervisor: warm slots, leader lock, reconcile, admit, activity."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from hca.config import FleetConfig
from hca.kanban import dispatch_tick
from hca.observe import list_expected_slots, status_rows
from hca.resources import admit, fetch_capacity
from hca.state import StateDB
from hca.tmux import TmuxManager


class Supervisor:
    def __init__(self, cfg: FleetConfig):
        self.cfg = cfg
        self.state_dir = Path(cfg.state_dir).expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db = StateDB(self.state_dir / "hca.sqlite")
        self.tmux = TmuxManager(cfg.tmux_socket)
        self._lock_fd: Optional[int] = None

    def is_draining(self) -> bool:
        return (self.state_dir / "DRAIN").exists()

    def acquire_leadership(self) -> bool:
        fd = self.db.try_leader_lock()
        if fd is None:
            return False
        self._lock_fd = fd
        return True

    def release_leadership(self) -> None:
        if self._lock_fd is not None:
            StateDB.release_leader_lock(self._lock_fd)
            self._lock_fd = None

    def warm_slots(self) -> list[str]:
        created = []
        self.tmux.ensure_server()
        for name in list_expected_slots(self.cfg):
            self.tmux.create_slot(name)
            created.append(name)
        self.db.set_activity(kind="fleet.warm", message=f"warmed {len(created)} slots")
        return created

    def reconcile(self) -> dict:
        rows = status_rows(self.cfg, self.db, self.tmux)
        # mark running mappings missing tmux/pid as crashed
        for rec in self.db.list_runs(status="running"):
            alive = self.tmux.has_session(rec.tmux_session)
            pid = self.tmux.pane_pid(rec.tmux_session) if alive else None
            if not alive:
                self.db.mark_run_status(
                    rec.board, rec.run_id, "crashed", error="tmux session missing"
                )
                self.db.set_activity(
                    kind="run.fail",
                    message=f"run {rec.run_id} crashed: tmux missing",
                    board=rec.board,
                    task_id=rec.task_id,
                    run_id=rec.run_id,
                    slot=rec.slot,
                )
            elif rec.pid and pid and rec.pid != pid:
                # pane was respawned — update pid
                rec.pid = pid
                rec.updated_at = time.time()
                self.db.upsert_run(rec)
        return {"slots": rows, "capacity": fetch_capacity(self.cfg).to_dict()}

    def can_admit(self, credits: float = 1.0) -> dict:
        if self.is_draining():
            return {
                "allowed": False,
                "reason": "waiting: fleet drain active (hca drain --clear to resume)",
                "credits": credits,
                "capacity": fetch_capacity(self.cfg).to_dict(),
            }
        decision = admit(self.cfg, self.db, credits=credits)
        return decision.to_dict()

    def tick(self, *, dispatch: bool = True) -> dict:
        if not self.acquire_leadership():
            return {"ok": False, "error": "another supervisor holds the leader lock"}
        try:
            if self.cfg.warm_slots:
                self.warm_slots()
            report = self.reconcile()
            decision_dict = self.can_admit()
            report["admission"] = decision_dict
            allowed = bool(decision_dict.get("allowed"))
            if dispatch and allowed:
                try:
                    report["dispatch"] = dispatch_tick(self.cfg, self.db, self.tmux)
                except Exception as exc:
                    report["dispatch"] = {"error": str(exc)}
            elif dispatch:
                report["dispatch"] = {
                    "skipped": True,
                    "reason": decision_dict.get("reason"),
                }
            report["ok"] = True
            report["drain"] = self.is_draining()
            os.environ.setdefault("HCA_STATE_DB", str(self.state_dir / "hca.sqlite"))
            os.environ.setdefault(
                "HCA_MAX_SUBAGENT_CREDITS", str(self.cfg.delegation_max_children)
            )
            return report
        finally:
            self.release_leadership()

    def run_forever(self) -> None:
        if not self.acquire_leadership():
            raise SystemExit("another supervisor holds the leader lock")
        try:
            if self.cfg.warm_slots:
                self.warm_slots()
            os.environ.setdefault("HCA_STATE_DB", str(self.state_dir / "hca.sqlite"))
            os.environ.setdefault(
                "HCA_MAX_SUBAGENT_CREDITS", str(self.cfg.delegation_max_children)
            )
            while True:
                self.reconcile()
                decision = self.can_admit()
                if decision.get("allowed"):
                    try:
                        dispatch_tick(self.cfg, self.db, self.tmux)
                    except Exception as exc:
                        self.db.set_activity(
                            kind="dispatch.error", message=str(exc)
                        )
                time.sleep(self.cfg.dispatch_interval_seconds)
        except KeyboardInterrupt:
            self.db.set_activity(kind="fleet.down", message="supervisor interrupted")
        finally:
            self.release_leadership()
