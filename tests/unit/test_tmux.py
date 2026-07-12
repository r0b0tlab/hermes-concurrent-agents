import shutil

import pytest

from hca.tmux import TmuxManager, sanitize_session_name


def test_sanitize_rejects_colons():
    assert ":" not in sanitize_session_name("hca:fleet:coder-01")
    assert sanitize_session_name("hca:fleet:coder-01").startswith("hca")


@pytest.mark.skipif(not shutil.which("tmux"), reason="tmux missing")
def test_slot_lifecycle():
    tm = TmuxManager(socket="hca-test-unit")
    name = "hca-test-coder-01"
    try:
        tm.kill_session(name)
    except Exception:
        pass
    tm.create_slot(name)
    assert tm.has_session(name)
    pid = tm.run_in_slot(name, "sleep 30")
    assert isinstance(pid, int) and pid > 0
    text = tm.capture_pane(name, lines=5)
    assert isinstance(text, str)
    tm.kill_session(name)
    assert not tm.has_session(name)
