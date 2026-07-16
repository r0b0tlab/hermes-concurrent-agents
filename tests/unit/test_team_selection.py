"""Team selection from bundled templates."""

from __future__ import annotations

import pytest

from hca.team import TeamError, available_teams, load_team, select_team


def test_bundled_teams_present():
    teams = available_teams()
    assert {"default", "small", "reviewed"} <= set(teams)


def test_default_team_shape():
    t = load_team("default")
    assert t.name == "default"
    assert t.role_of_kind("planner") is not None
    assert t.role_of_kind("reviewer") is not None
    assert t.worker_count() >= 1
    assert t.requires_review()


def test_small_team_is_single_worker():
    t = load_team("small")
    assert t.worker_count() == 1


def test_reviewed_team_review_always():
    t = load_team("reviewed")
    assert t.review_policy == "always"
    assert t.requires_review()


def test_unknown_team_raises_with_known_set():
    with pytest.raises(TeamError) as exc:
        load_team("nonexistent")
    assert "available" in str(exc.value)


def test_review_policy_override():
    t = select_team("default", review_policy="never")
    assert t.review_policy == "never"
    # empty name defaults to 'default'
    assert select_team("").name == "default"
