"""Transactional HCA state migrations: upgrade, refuse-future, rollback."""

from __future__ import annotations

import sqlite3

import pytest

from hca.migrations import (
    CURRENT_SCHEMA_VERSION,
    Migration,
    MigrationError,
    apply_migrations,
    current_version,
)
from hca.state import SCHEMA, StateDB


def _open(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _make_v1(path):
    """Create a legacy v1 DB (base schema, version marker 1)."""
    conn = _open(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version','1')")
    # some existing data that must survive migration
    conn.execute(
        "INSERT INTO activity(ts, kind, message, data_json) VALUES (1.0,'x','legacy','{}')"
    )
    conn.commit()
    conn.close()


def test_fresh_db_is_current(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o755)
    db = StateDB(state_dir / "hca.sqlite")
    assert state_dir.stat().st_mode & 0o077 == 0
    assert db.path.stat().st_mode & 0o077 == 0
    conn = _open(db.path)
    try:
        assert current_version(conn) == CURRENT_SCHEMA_VERSION
        # run projection tables exist
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"hca_runs", "hca_questions", "hca_run_events"} <= names
        run_columns = {
            r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        assert "pid_start_ticks" in run_columns
    finally:
        conn.close()


def test_upgrade_v1_preserves_data(tmp_path):
    path = tmp_path / "hca.sqlite"
    _make_v1(path)
    # Opening through StateDB triggers the forward migration.
    StateDB(path)
    conn = _open(path)
    try:
        assert current_version(conn) == CURRENT_SCHEMA_VERSION
        row = conn.execute("SELECT message FROM activity WHERE kind='x'").fetchone()
        assert row["message"] == "legacy"  # data preserved
        run_columns = {
            r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        assert "pid_start_ticks" in run_columns
        # a backup file was written next to the DB
    finally:
        conn.close()
    backups = list(tmp_path.glob("hca.sqlite.bak-*"))
    assert backups, "expected a pre-migration backup"


def test_unknown_future_version_is_refused(tmp_path):
    path = tmp_path / "hca.sqlite"
    _make_v1(path)
    conn = _open(path)
    conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(MigrationError):
        StateDB(path)


def test_interrupted_migration_rolls_back(tmp_path):
    path = tmp_path / "hca.sqlite"
    _make_v1(path)

    def boom(conn):
        raise RuntimeError("disk full mid-migration")

    failing = [Migration(version=2, name="boom", up_fn=boom)]
    conn = _open(path)
    try:
        with pytest.raises(MigrationError):
            apply_migrations(path, conn, target=2, migrations=failing)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    # DB restored from backup: version still 1, legacy data intact
    conn2 = _open(path)
    try:
        assert current_version(conn2) == 1
        row = conn2.execute("SELECT message FROM activity WHERE kind='x'").fetchone()
        assert row["message"] == "legacy"
    finally:
        conn2.close()


def test_no_op_when_already_current(tmp_path):
    db = StateDB(tmp_path / "hca.sqlite")
    conn = _open(db.path)
    try:
        applied = apply_migrations(db.path, conn, target=CURRENT_SCHEMA_VERSION)
        assert applied == []
    finally:
        conn.close()


def test_failed_migration_restores_wal_state_and_keeps_connection_usable(tmp_path):
    path = tmp_path / "hca.sqlite"
    db = StateDB(path)
    conn = _open(db.path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "INSERT INTO activity(ts, kind, message, data_json) "
        "VALUES (2.0, 'wal', 'committed-in-wal', '{}')"
    )
    conn.commit()

    def mutate_then_fail(c):
        c.execute("DELETE FROM activity WHERE kind='wal'")
        raise RuntimeError("crash after mutation")

    failing = [
        Migration(
            version=CURRENT_SCHEMA_VERSION + 1,
            name="wal-boom",
            up_fn=mutate_then_fail,
        )
    ]
    with pytest.raises(MigrationError):
        apply_migrations(
            path,
            conn,
            target=CURRENT_SCHEMA_VERSION + 1,
            migrations=failing,
        )

    # apply_migrations owns neither the connection nor its lifetime.
    row = conn.execute(
        "SELECT message FROM activity WHERE kind='wal'"
    ).fetchone()
    assert row["message"] == "committed-in-wal"
    assert current_version(conn) == CURRENT_SCHEMA_VERSION
    conn.execute("SELECT 1").fetchone()  # still usable
    conn.close()

    backups = list(tmp_path.glob("hca.sqlite.bak-*"))
    assert backups
    assert backups[-1].stat().st_mode & 0o077 == 0
