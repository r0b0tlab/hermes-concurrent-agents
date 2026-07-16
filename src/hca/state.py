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
    pid_start_ticks: Optional[int] = None


class StateDB:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # HCA state can contain prompts, paths, and run metadata. Tighten an
        # existing permissive directory rather than relying on the caller's
        # umask; never make it more permissive.
        self.path.parent.chmod(self.path.parent.stat().st_mode & ~0o077)
        self._init()
        self._tighten_db_files()

    def _tighten_db_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                candidate.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        from hca.migrations import (
            CURRENT_SCHEMA_VERSION,
            apply_migrations,
            current_version,
        )

        with self._connect() as conn:
            # Base (v1) schema is idempotent CREATE IF NOT EXISTS.
            conn.executescript(SCHEMA)
            ver = current_version(conn)
            if ver == 0:
                # Fresh DB: stamp the base version, then apply forward
                # migrations to reach CURRENT (adds run projection tables).
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '1')"
                )
                conn.commit()
        # Migrate forward transactionally (own connection/txn per step). This
        # never resets the version marker and refuses unknown future versions.
        with self._connect() as conn:
            apply_migrations(self.path, conn, target=CURRENT_SCHEMA_VERSION)

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
                  last_activity, error, pid_start_ticks
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(board, run_id) DO UPDATE SET
                  slot=excluded.slot,
                  node=excluded.node,
                  tmux_session=excluded.tmux_session,
                  pid=excluded.pid,
                  pid_start_ticks=excluded.pid_start_ticks,
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
                    rec.pid_start_ticks,
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

    def latest_run_for_task(self, board: str, task_id: str) -> Optional[RunRecord]:
        """Most-recent HCA run mapping for a task, regardless of status.

        Used to recover the integer run id + bound worker pid that were
        captured at spawn time — proof-of-execution evidence that upstream
        nulls off the task row when the run completes.
        """
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE board=? AND task_id=? "
                "ORDER BY started_at DESC LIMIT 1",
                (board, task_id),
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

    def release_lease_prefix(self, prefix: str) -> int:
        """Release every lease whose id starts with ``prefix``.

        Used to release a worker's durable lease by (board, task) regardless of
        which run id it carried — exact release on terminal/crash/stop.
        """
        like = prefix.replace("%", r"\%").replace("_", r"\_") + "%"
        with self.connection() as conn:
            cur = conn.execute(
                "DELETE FROM leases WHERE lease_id LIKE ? ESCAPE '\\'", (like,)
            )
            return cur.rowcount

    def release_leases_by_owner(self, owner: str, *, kind: Optional[str] = None) -> int:
        """Release exact child/reservation leases owned by one parent worker."""
        with self.connection() as conn:
            if kind is None:
                cur = conn.execute("DELETE FROM leases WHERE owner=?", (owner,))
            else:
                cur = conn.execute(
                    "DELETE FROM leases WHERE owner=? AND kind=?", (owner, kind)
                )
            return cur.rowcount

    def list_leases(self, kind: Optional[str] = None) -> list[dict[str, Any]]:
        now = time.time()
        with self.connection() as conn:
            conn.execute(
                "DELETE FROM leases WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            q = "SELECT * FROM leases"
            args: list[Any] = []
            if kind:
                q += " WHERE kind=?"
                args.append(kind)
            rows = conn.execute(q, args).fetchall()
        return [
            {
                "lease_id": r["lease_id"],
                "kind": r["kind"],
                "owner": r["owner"],
                "credits": r["credits"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
                "meta": json.loads(r["meta_json"] or "{}"),
            }
            for r in rows
        ]

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

    def get_meta(self, key: str, default: str = "") -> str:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                (key, value),
            )

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
            pid_start_ticks=row["pid_start_ticks"],
        )
