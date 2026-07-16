"""Durable top-level worker leases.

A lease is the durable admission credit for one launched worker. Unlike the
in-memory per-tick :class:`~hca.routing.Reservations` (which only prevents
double-booking a slot within a single dispatch tick), a lease survives process
restarts in the HCA state DB and is what :func:`hca.resources.admit` counts
against the sequence-credit ceiling. It is acquired at spawn — bound to
board/task/run/slot/node/pid — and released exactly on terminal, crash, or
stop, so a launched worker always consumes exactly one credit for its lifetime.
"""

from __future__ import annotations

from typing import Optional

from hca.state import StateDB

LEASE_KIND_WORKER = "worker"


def worker_lease_id(board: str, task_id: str, run_id: object) -> str:
    return f"run:{board}:{task_id}:{run_id}"


def worker_lease_prefix(board: str, task_id: str) -> str:
    """Prefix matching every run's lease for a (board, task)."""
    return f"run:{board}:{task_id}:"


def acquire_worker_lease(
    state: StateDB,
    *,
    board: str,
    task_id: str,
    run_id: object,
    slot: str,
    pid: Optional[int],
    node: str = "local",
    credits: float = 1.0,
) -> bool:
    """Acquire the durable lease for a spawned worker. Idempotent per run id."""
    lease_id = worker_lease_id(board, task_id, run_id)
    return state.acquire_lease(
        lease_id,
        kind=LEASE_KIND_WORKER,
        owner=slot,
        credits=credits,
        meta={
            "board": board,
            "task_id": task_id,
            "run_id": str(run_id),
            "slot": slot,
            "node": node,
            "pid": pid,
        },
    )


def release_worker_lease(state: StateDB, *, board: str, task_id: str) -> int:
    """Release every lease this (board, task) holds, whatever the run id."""
    return state.release_lease_prefix(worker_lease_prefix(board, task_id))
