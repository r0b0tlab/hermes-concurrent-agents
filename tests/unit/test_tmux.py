import shutil
import time
import uuid

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


@pytest.mark.skipif(not shutil.which("tmux"), reason="tmux missing")
def test_run_in_slot_can_remove_inherited_tui_mode(monkeypatch):
    monkeypatch.setenv("HERMES_TUI", "1")
    socket = f"hca-test-env-{uuid.uuid4().hex[:8]}"
    tm = TmuxManager(socket=socket)
    name = "hca-test-env-slot"
    try:
        tm.create_slot(name)
        tm.run_in_slot(
            name,
            "printf 'HERMES_TUI=%s\\n' \"${HERMES_TUI-unset}\"; sleep 30",
            unset_env=["HERMES_TUI"],
        )
        text = ""
        for _ in range(20):
            text = tm.capture_pane(name, lines=5)
            if "HERMES_TUI=" in text:
                break
            time.sleep(0.05)
        assert "HERMES_TUI=unset" in text
    finally:
        tm.kill_session(name)
