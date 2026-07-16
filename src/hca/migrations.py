"""Ordered, transactional HCA state migrations.

Replaces the previous ``INSERT OR REPLACE schema_version='1'`` on every open
(which made upgrades unsafe). Migrations:

  * back up the DB file first,
  * refuse an unknown *future* schema version (fail closed),
  * apply each step ``current+1 .. target`` in its own transaction,
  * verify integrity + expected tables afterward,
  * restore the backup and re-raise on any failure (rollback before startup),
  * only ever move the version marker forward.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

CURRENT_SCHEMA_VERSION = 2

VERSION_KEY = "schema_version"


class MigrationError(RuntimeError):
    pass


@dataclass
class Migration:
    version: int
    name: str
    up_sql: str = ""
    up_fn: Optional[Callable[[sqlite3.Connection], None]] = None


# v1 is the base schema created by StateDB.SCHEMA. v2 adds the run projection
# tables (previously created ad hoc by RunStore).
_RUN_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS hca_runs (
  run_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  goal TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  reason TEXT,
  idempotency_key TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hca_runs_idem ON hca_runs(idempotency_key)
  WHERE idempotency_key IS NOT NULL AND idempotency_key != '';
CREATE TABLE IF NOT EXISTS hca_questions (
  question_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_id TEXT,
  prompt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  answer TEXT,
  created_at REAL NOT NULL,
  answered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_hca_questions_run ON hca_questions(run_id);
CREATE TABLE IF NOT EXISTS hca_run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  message TEXT,
  data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_hca_run_events_run ON hca_run_events(run_id);
"""

MIGRATIONS: list[Migration] = [
    Migration(version=2, name="run_projection_tables", up_sql=_RUN_TABLES_SQL),
]


def current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (VERSION_KEY,)
        ).fetchone()
    except sqlite3.Error:
        return 0
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
        (VERSION_KEY, str(version)),
    )


def _verify(conn: sqlite3.Connection, *, target: int) -> None:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    if not row or str(row[0]).lower() != "ok":
        raise MigrationError(f"integrity check failed after migration: {row}")
    if current_version(conn) != target:
        raise MigrationError(
            f"schema marker is v{current_version(conn)}, expected v{target}"
        )
    if target >= 2:
        tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = {"hca_runs", "hca_questions", "hca_run_events"} - tables
        if missing:
            raise MigrationError(
                "migration verification missing table(s): " + ", ".join(sorted(missing))
            )


def _execute_script_transactionally(conn: sqlite3.Connection, script: str) -> None:
    """Execute a SQL script without ``executescript``'s implicit COMMIT.

    ``sqlite3.Connection.executescript`` commits any pending transaction before
    running the script, so wrapping it in ``with conn`` does *not* make a
    migration atomic.  Split only at statements SQLite itself reports complete
    and execute each statement through ``execute`` inside our explicit txn.
    """
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            pending = ""
            if statement:
                conn.execute(statement)
    if pending.strip():
        raise MigrationError("incomplete SQL statement in migration")


def _sqlite_backup(conn: sqlite3.Connection, path: Path) -> None:
    """Take a WAL-safe, transactionally consistent SQLite backup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(path))
    try:
        conn.backup(target)
    finally:
        target.close()
    path.chmod(0o600)


def _sqlite_restore(conn: sqlite3.Connection, path: Path) -> None:
    """Restore into the caller-owned connection without closing it."""
    try:
        conn.rollback()
    except sqlite3.Error:
        pass
    source = sqlite3.connect(str(path))
    try:
        source.backup(conn)
    finally:
        source.close()


def apply_migrations(
    db_path: str | Path,
    conn: sqlite3.Connection,
    *,
    target: int = CURRENT_SCHEMA_VERSION,
    migrations: Optional[list[Migration]] = None,
    backup: bool = True,
) -> list[str]:
    """Migrate the DB to ``target``. Returns the names of applied steps.

    ``migrations`` is injectable for testing (e.g. a deliberately failing
    step to exercise rollback). On any failure the DB file is restored from
    the pre-migration backup and a MigrationError is raised.
    """
    steps = migrations if migrations is not None else MIGRATIONS
    cur = current_version(conn)

    if cur > target:
        raise MigrationError(
            f"HCA state schema v{cur} is newer than this build supports "
            f"(v{target}). Refusing to open — upgrade HCA rather than "
            "downgrading the on-disk state."
        )
    if cur == target:
        return []

    path = Path(db_path)
    backup_path: Optional[Path] = None
    if backup and path.exists():
        backup_path = path.with_suffix(
            path.suffix + f".bak-{time.time_ns()}"
        )
        _sqlite_backup(conn, backup_path)

    applied: list[str] = []
    try:
        for mig in sorted(steps, key=lambda m: m.version):
            if mig.version <= cur or mig.version > target:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                if mig.up_sql:
                    _execute_script_transactionally(conn, mig.up_sql)
                if mig.up_fn is not None:
                    mig.up_fn(conn)
                _set_version(conn, mig.version)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            applied.append(mig.name)
        _verify(conn, target=target)
    except Exception as exc:
        if backup_path is not None and backup_path.exists():
            _sqlite_restore(conn, backup_path)
            _verify(conn, target=cur)
        raise MigrationError(
            f"migration failed ({exc}); "
            + ("restored pre-migration DB" if backup_path else "transaction rolled back")
        ) from exc
    return applied
