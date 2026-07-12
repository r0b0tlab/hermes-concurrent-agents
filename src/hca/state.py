"""SQLite reconciliation ledger for HCA control mappings (not Kanban truth)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  board TEXT NOT NULL,
  task_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  slot TEXT NOT NULL,
  node TEXT NOT NULL DEFAULT 'local',
  tmux_session TEXT NOT NULL,
  pid INTEGER,
  hermes_session_id TEXT,
  workspace TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  started_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  last_activity TEXT,
  error TEXT,
  PRIMARY KEY (board, run_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_live_slot
  ON runs(slot) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS leases (
  lease_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  owner TEXT NOT NULL,
  credits REAL NOT NULL DEFAULT 1.0,
  created_at REAL NOT NULL,
  expires_at REAL,
  meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  board TEXT,
  task_id TEXT,
  run_id TEXT,
  slot TEXT,
  node TEXT,
  message TEXT,
  data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS nodes (
  host TEXT PRIMARY KEY,
  last_probe_at REAL,
  reachable INTEGER NOT NULL DEFAULT 0,
  status_json TEXT NOT NULL DEFAULT '{}'
);
"""


@dataclass
class RunRecord:
    board: str
    task_id: str
    run_id: str
    slot: str
    node: str
    tmux_session: str
    pid: Optional[int]
    hermes_session_id: Optional[str]
    workspace: Optional[str]
    status: str
    started_at: float
    updated_at: float
    last_activity: Optional[str]
    error: Optional[str]


class StateDB:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '1')"
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_run(self, rec: RunRecord) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO runs(
                  board, task_id, run_id, slot, node, tmux_session, pid,
                  hermes_session_id, workspace, status, started_at, updated_at,
                  last_activity, error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(board, run_id) DO UPDATE SET
                  slot=excluded.slot,
                  node=excluded.node,
                  tmux_session=excluded.tmux_session,
                  pid=excluded.pid,
                  hermes_session_id=excluded.hermes_session_id,
                  workspace=excluded.workspace,
                  status=excluded.status,
                  updated_at=excluded.updated_at,
                  last_activity=excluded.last_activity,
                  error=excluded.error
                """,
                (
                    rec.board,
                    rec.task_id,
                    rec.run_id,
                    rec.slot,
                    rec.node,
                    rec.tmux_session,
                    rec.pid,
                    rec.hermes_session_id,
                    rec.workspace,
                    rec.status,
                    rec.started_at,
                    rec.updated_at,
                    rec.last_activity,
                    rec.error,
                ),
            )

    def list_runs(self, *, status: Optional[str] = "running") -> list[RunRecord]:
        q = "SELECT * FROM runs"
        args: list[Any] = []
        if status:
            q += " WHERE status = ?"
            args.append(status)
        q += " ORDER BY started_at DESC"
        with self.connection() as conn:
            rows = conn.execute(q, args).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_run(self, board: str, run_id: str) -> Optional[RunRecord]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE board=? AND run_id=?",
                (board, run_id),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def mark_run_status(
        self, board: str, run_id: str, status: str, error: str = ""
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE runs SET status=?, error=?, updated_at=?
                WHERE board=? AND run_id=?
                """,
                (status, error or None, time.time(), board, run_id),
            )

    def set_activity(
        self,
        *,
        kind: str,
        message: str = "",
        board: str = "",
        task_id: str = "",
        run_id: str = "",
        slot: str = "",
        node: str = "",
        data: Optional[dict] = None,
    ) -> None:
        ts = time.time()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO activity(ts, kind, board, task_id, run_id, slot, node, message, data_json)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    kind,
                    board,
                    task_id,
                    run_id,
                    slot,
                    node,
                    message,
                    json.dumps(data or {}),
                ),
            )
            if run_id and board:
                conn.execute(
                    """
                    UPDATE runs SET last_activity=?, updated_at=?
                    WHERE board=? AND run_id=?
                    """,
                    (message or kind, ts, board, run_id),
                )

    def recent_activity(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM activity ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "kind": r["kind"],
                    "board": r["board"],
                    "task_id": r["task_id"],
                    "run_id": r["run_id"],
                    "slot": r["slot"],
                    "node": r["node"],
                    "message": r["message"],
                    "data": json.loads(r["data_json"] or "{}"),
                }
            )
        return out

    def acquire_lease(
        self,
        lease_id: str,
        kind: str,
        owner: str,
        credits: float = 1.0,
        ttl_seconds: Optional[float] = None,
        meta: Optional[dict] = None,
    ) -> bool:
        now = time.time()
        expires = (now + ttl_seconds) if ttl_seconds else None
        try:
            with self.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO leases(lease_id, kind, owner, credits, created_at, expires_at, meta_json)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        lease_id,
                        kind,
                        owner,
                        credits,
                        now,
                        expires,
                        json.dumps(meta or {}),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def release_lease(self, lease_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM leases WHERE lease_id=?", (lease_id,))

    def active_lease_credits(self, kind: Optional[str] = None) -> float:
        now = time.time()
        with self.connection() as conn:
            conn.execute(
                "DELETE FROM leases WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            if kind:
                row = conn.execute(
                    "SELECT COALESCE(SUM(credits),0) AS c FROM leases WHERE kind=?",
                    (kind,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(credits),0) AS c FROM leases"
                ).fetchone()
        return float(row["c"] if row else 0.0)

    def leader_lock_path(self) -> Path:
        return self.path.parent / "leader.lock"

    def try_leader_lock(self) -> Optional[int]:
        """Best-effort exclusive lock file. Returns fd if acquired."""
        path = self.leader_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            return fd
        except OSError:
            os.close(fd)
            return None

    @staticmethod
    def release_leader_lock(fd: int) -> None:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            board=row["board"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            slot=row["slot"],
            node=row["node"],
            tmux_session=row["tmux_session"],
            pid=row["pid"],
            hermes_session_id=row["hermes_session_id"],
            workspace=row["workspace"],
            status=row["status"],
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            last_activity=row["last_activity"],
            error=row["error"],
        )
