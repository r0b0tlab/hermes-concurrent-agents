from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from hca.kanban_orchestrator import KanbanOrchestrator
from hca.worker_launch import WorkerLaunchError, attest_worker_workspace


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "hca@example.invalid")
    _git(repo, "config", "user.name", "HCA Test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "base")
    return repo


def _task(task_id="t_child"):
    return SimpleNamespace(id=task_id, workspace_kind="worktree")


def test_worker_workspace_rejects_primary_checkout(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(WorkerLaunchError, match="primary checkout"):
        attest_worker_workspace(_task(), str(repo))


def test_worker_workspace_accepts_registered_child_location(tmp_path):
    repo = _repo(tmp_path)
    child = repo / ".worktrees" / "t_child"
    _git(repo, "worktree", "add", "-qb", "wt/t_child", str(child))
    assert attest_worker_workspace(_task(), str(child)) == str(child.resolve())


def test_worker_workspace_rejects_linked_canonical_checkout(tmp_path):
    repo = _repo(tmp_path)
    canonical = tmp_path / "canonical-contract"
    _git(repo, "worktree", "add", "-qb", "canonical", str(canonical))
    with pytest.raises(WorkerLaunchError, match=r"not under.*\.worktrees"):
        attest_worker_workspace(_task(), str(canonical))


def test_linked_submitted_project_is_reanchored_on_primary_checkout(tmp_path):
    repo = _repo(tmp_path)
    canonical = tmp_path / "canonical-contract"
    _git(repo, "worktree", "add", "-qb", "canonical", str(canonical))
    kind, anchor = KanbanOrchestrator._workspace_for_spec(
        SimpleNamespace(project_root=str(canonical))
    )
    assert kind == "worktree"
    assert anchor == str(repo.resolve())
