import os
import subprocess

from hca.kanban_orchestrator import KanbanOrchestrator
from hca.process_identity import proc_start_ticks, process_group_alive


def _orchestrator(tmp_path):
    return KanbanOrchestrator.__new__(KanbanOrchestrator)


def test_worker_pid_reuse_identity_mismatch_is_never_signalled(tmp_path):
    sleeper = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        ticks = proc_start_ticks(sleeper.pid)
        assert ticks is not None
        outcome = _orchestrator(tmp_path)._terminate_process_group(
            sleeper.pid,
            expected_start_ticks=ticks + 1,
            grace=0.01,
        )
        assert outcome == "identity_mismatch"
        assert sleeper.poll() is None
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


def test_exact_worker_stop_terminates_owned_descendant_group(tmp_path):
    parent = subprocess.Popen(
        ["bash", "-lc", "sleep 30 & wait"],
        start_new_session=True,
    )
    try:
        ticks = proc_start_ticks(parent.pid)
        assert ticks is not None
        pgid = os.getpgid(parent.pid)
        assert process_group_alive(pgid)
        outcome = _orchestrator(tmp_path)._terminate_process_group(
            parent.pid,
            expected_start_ticks=ticks,
            grace=1.0,
        )
        assert outcome in {"terminated", "killed"}
        assert not process_group_alive(pgid)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)