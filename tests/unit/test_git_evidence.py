from __future__ import annotations

import subprocess
from types import SimpleNamespace

from hca.kanban_orchestrator import validate_git_result


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
    (repo / "result.txt").write_text("accepted\n", encoding="utf-8")
    _git(repo, "add", "result.txt")
    _git(repo, "commit", "-qm", "accepted result")
    return repo, _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD^{tree}")


def test_valid_git_result_binds_head_tree_and_workspace(tmp_path):
    repo, commit, tree = _repo(tmp_path)
    task = SimpleNamespace(workspace_kind="worktree", workspace_path=str(repo))

    valid, reason, artifacts = validate_git_result(
        task, f"HCA_RESULT_COMMIT: {commit}\nchecks passed"
    )

    assert valid is True
    assert reason == ""
    assert [(a.kind, a.ref) for a in artifacts] == [
        ("worktree", str(repo.resolve())),
        ("git_commit", commit),
        ("git_tree", tree),
    ]


def test_git_result_requires_marker_on_first_nonempty_line(tmp_path):
    repo, commit, _ = _repo(tmp_path)
    task = SimpleNamespace(workspace_kind="worktree", workspace_path=str(repo))

    valid, reason, artifacts = validate_git_result(
        task, f"checks passed\nHCA_RESULT_COMMIT: {commit}"
    )

    assert valid is False
    assert "first non-empty line" in reason
    assert artifacts == []


def test_git_result_rejects_nonexistent_commit(tmp_path):
    repo, _commit, _ = _repo(tmp_path)
    task = SimpleNamespace(workspace_kind="worktree", workspace_path=str(repo))

    valid, reason, artifacts = validate_git_result(
        task, "HCA_RESULT_COMMIT: " + ("a" * 40)
    )

    assert valid is False
    assert "does not exist" in reason
    assert artifacts == []


def test_git_result_rejects_commit_that_is_not_workspace_head(tmp_path):
    repo, old_commit, _ = _repo(tmp_path)
    (repo / "result.txt").write_text("new head\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "new head")
    task = SimpleNamespace(workspace_kind="worktree", workspace_path=str(repo))

    valid, reason, artifacts = validate_git_result(
        task, f"HCA_RESULT_COMMIT: {old_commit}"
    )

    assert valid is False
    assert "not workspace HEAD" in reason
    assert artifacts == []
