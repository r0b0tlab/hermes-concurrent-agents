"""Immutable run spec, finite run-state projection, and durable run store.

`RunSpec` is the immutable record of *what was asked*: goal, constraints,
acceptance criteria, project, selected profiles, budgets, and idempotency
metadata. `RunState` is a versioned finite projection over that spec plus
task/question/event rows — it is not a second task-lifecycle authority.

The store keeps its own idempotent tables (`hca_runs`, `hca_questions`,
`hca_run_events`) inside the HCA state DB. State transitions are validated
against an explicit machine so a run can never jump from, say, `completed`
back to `running`.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

RUN_SCHEMA_VERSION = 3


class RunState(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    REVIEW = "review"
    REWORK = "rework"
    STOPPING = "stopping"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[RunState] = frozenset(
    {RunState.COMPLETED, RunState.BLOCKED, RunState.FAILED, RunState.CANCELLED}
)

# Explicit transition machine. `completed` requires accepted verification;
# cancellation can only arrive via `stopping`. Any state may go to failed
# (crash) or blocked (deadlock/needs-attention) — those are recorded, not
# silently swallowed.
_ALWAYS = frozenset({RunState.FAILED, RunState.BLOCKED})
VALID_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.QUEUED: frozenset({RunState.PLANNING, RunState.STOPPING}) | _ALWAYS,
    RunState.PLANNING: frozenset({RunState.RUNNING, RunState.NEEDS_INPUT, RunState.STOPPING}) | _ALWAYS,
    RunState.RUNNING: frozenset(
        {RunState.REVIEW, RunState.NEEDS_INPUT, RunState.REWORK, RunState.STOPPING, RunState.COMPLETED}
    ) | _ALWAYS,
    RunState.NEEDS_INPUT: frozenset({RunState.RUNNING, RunState.PLANNING, RunState.STOPPING}) | _ALWAYS,
    RunState.REVIEW: frozenset({RunState.REWORK, RunState.COMPLETED, RunState.STOPPING}) | _ALWAYS,
    RunState.REWORK: frozenset({RunState.RUNNING, RunState.REVIEW, RunState.STOPPING}) | _ALWAYS,
    RunState.STOPPING: frozenset({RunState.CANCELLED, RunState.COMPLETED}) | _ALWAYS,
    # terminal
    RunState.COMPLETED: frozenset(),
    RunState.BLOCKED: frozenset({RunState.PLANNING, RunState.RUNNING, RunState.STOPPING}),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


class RunStateError(RuntimeError):
    pass


def can_transition(src: RunState, dst: RunState) -> bool:
    if src == dst:
        return True
    return dst in VALID_TRANSITIONS.get(src, frozenset())


@dataclass
class RunBudgets:
    max_tasks: int = 20
    max_workers: int = 4
    wall_seconds: int = 3600
    max_turns_per_task: int = 200
    max_retries: int = 2
    max_review_cycles: int = 2
    max_disk_mb: int = 5000
    max_subagents: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "RunBudgets":
        d = d or {}
        known = {f: d[f] for f in cls().to_dict() if f in d}
        return cls(**known)


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    goal: str
    project_root: str = ""
    project_ref: str = ""
    constraints: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    independent_criteria: bool = False
    source_profiles: tuple[str, ...] = ()
    team: str = "default"
    concurrency: int = 1
    review_policy: str = "auto"  # auto | always | never
    input_policy: str = "allow"  # allow | fail_closed
    budgets: RunBudgets = field(default_factory=RunBudgets)
    idempotency_key: str = ""
    board: str = "hca"
    created_at: float = 0.0
    schema_version: int = RUN_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["constraints"] = list(self.constraints)
        d["acceptance_criteria"] = list(self.acceptance_criteria)
        d["source_profiles"] = list(self.source_profiles)
        d["budgets"] = self.budgets.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunSpec":
        return cls(
            run_id=d["run_id"],
            goal=d["goal"],
            project_root=d.get("project_root", ""),
            project_ref=d.get("project_ref", ""),
            constraints=tuple(d.get("constraints") or ()),
            acceptance_criteria=tuple(d.get("acceptance_criteria") or ()),
            independent_criteria=bool(d.get("independent_criteria", False)),
            source_profiles=tuple(d.get("source_profiles") or ()),
            team=d.get("team", "default"),
            concurrency=int(d.get("concurrency", 1)),
            review_policy=d.get("review_policy", "auto"),
            input_policy=d.get("input_policy", "allow"),
            budgets=RunBudgets.from_dict(d.get("budgets")),
            idempotency_key=d.get("idempotency_key", ""),
            board=d.get("board", "hca"),
            created_at=float(d.get("created_at", 0.0)),
            schema_version=int(d.get("schema_version", RUN_SCHEMA_VERSION)),
        )


def new_run_id() -> str:
    return f"run-{int(time.time())}-{uuid.uuid4().hex[:8]}"


@dataclass
class Question:
    question_id: str
    run_id: str
    prompt: str
    task_id: str = ""
    status: str = "open"  # open | answered
    answer: str = ""
    created_at: float = 0.0
    answered_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunProjection:
    run_id: str
    state: RunState
    goal: str
    created_at: float
    updated_at: float
    reason: str = ""
    idempotency_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


_STORE_SCHEMA = """
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


class RunStore:
    """Durable run projection store (idempotent tables in the HCA state DB)."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        import sqlite3

        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_STORE_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # --- runs ---

    def find_by_idempotency_key(self, key: str) -> Optional[RunProjection]:
        if not key:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM hca_runs WHERE idempotency_key = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_proj(row) if row else None

    def create_run(self, spec: RunSpec, state: RunState = RunState.QUEUED) -> RunProjection:
        now = spec.created_at or time.time()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO hca_runs(run_id, state, goal, spec_json, reason, "
                "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    spec.run_id,
                    state.value,
                    spec.goal,
                    json.dumps(spec.to_dict()),
                    "",
                    spec.idempotency_key or "",
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.append_event(spec.run_id, "run.created", f"queued: {spec.goal[:80]}")
        return RunProjection(
            run_id=spec.run_id,
            state=state,
            goal=spec.goal,
            created_at=now,
            updated_at=now,
            idempotency_key=spec.idempotency_key or "",
        )

    def get_spec(self, run_id: str) -> Optional[RunSpec]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT spec_json FROM hca_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return RunSpec.from_dict(json.loads(row["spec_json"]))

    def get_run(self, run_id: str) -> Optional[RunProjection]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM hca_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_proj(row) if row else None

    def set_state(
        self, run_id: str, new_state: RunState, *, reason: str = ""
    ) -> RunProjection:
        proj = self.get_run(run_id)
        if proj is None:
            raise RunStateError(f"unknown run {run_id}")
        if not can_transition(proj.state, new_state):
            raise RunStateError(
                f"illegal run transition {proj.state.value} -> {new_state.value} "
                f"for {run_id}"
            )
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE hca_runs SET state=?, reason=?, updated_at=? WHERE run_id=?",
                (new_state.value, reason or proj.reason, now, run_id),
            )
            conn.commit()
        finally:
            conn.close()
        self.append_event(run_id, "run.state", f"{proj.state.value} -> {new_state.value}: {reason}")
        proj.state = new_state
        proj.reason = reason or proj.reason
        proj.updated_at = now
        return proj

    def list_runs(self, *, limit: int = 100) -> list[RunProjection]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM hca_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_proj(r) for r in rows]

    # --- questions ---

    def add_question(self, run_id: str, prompt: str, *, task_id: str = "") -> Question:
        qid = f"q-{uuid.uuid4().hex[:10]}"
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO hca_questions(question_id, run_id, task_id, prompt, "
                "status, answer, created_at, answered_at) VALUES (?,?,?,?,?,?,?,?)",
                (qid, run_id, task_id, prompt, "open", "", now, None),
            )
            conn.commit()
        finally:
            conn.close()
        self.append_event(run_id, "run.needs_input", f"question {qid}: {prompt[:80]}")
        return Question(
            question_id=qid, run_id=run_id, task_id=task_id, prompt=prompt,
            status="open", created_at=now,
        )

    def get_question(self, question_id: str) -> Optional[Question]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM hca_questions WHERE question_id = ?", (question_id,)
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_question(row) if row else None

    def answer_question(self, run_id: str, question_id: str, answer: str) -> Question:
        q = self.get_question(question_id)
        if q is None:
            raise RunStateError(f"unknown question {question_id}")
        if q.run_id != run_id:
            raise RunStateError(
                f"question {question_id} belongs to run {q.run_id}, not {run_id}"
            )
        if q.status == "answered":
            raise RunStateError(f"question {question_id} already answered")
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE hca_questions SET status='answered', answer=?, answered_at=? "
                "WHERE question_id=?",
                (answer, now, question_id),
            )
            conn.commit()
        finally:
            conn.close()
        self.append_event(run_id, "run.responded", f"answered {question_id}")
        q.status = "answered"
        q.answer = answer
        q.answered_at = now
        return q

    def open_questions(self, run_id: str) -> list[Question]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM hca_questions WHERE run_id=? AND status='open' "
                "ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_question(r) for r in rows]

    def list_questions(self, run_id: str) -> list[Question]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM hca_questions WHERE run_id=? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_question(r) for r in rows]

    # --- events ---

    def append_event(
        self, run_id: str, kind: str, message: str = "", data: Optional[dict] = None
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO hca_run_events(run_id, ts, kind, message, data_json) "
                "VALUES (?,?,?,?,?)",
                (run_id, time.time(), kind, message, json.dumps(data or {})),
            )
            conn.commit()
        finally:
            conn.close()

    def list_events(self, run_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM hca_run_events WHERE run_id=? ORDER BY id ASC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "ts": r["ts"],
                "kind": r["kind"],
                "message": r["message"],
                "data": json.loads(r["data_json"] or "{}"),
            }
            for r in rows
        ]

    # --- helpers ---

    @staticmethod
    def _row_to_proj(row) -> RunProjection:
        return RunProjection(
            run_id=row["run_id"],
            state=RunState(row["state"]),
            goal=row["goal"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reason=row["reason"] or "",
            idempotency_key=row["idempotency_key"] or "",
        )

    @staticmethod
    def _row_to_question(row) -> Question:
        return Question(
            question_id=row["question_id"],
            run_id=row["run_id"],
            task_id=row["task_id"] or "",
            prompt=row["prompt"],
            status=row["status"],
            answer=row["answer"] or "",
            created_at=row["created_at"],
            answered_at=row["answered_at"] or 0.0,
        )
