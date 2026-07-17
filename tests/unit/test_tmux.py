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


@pytest.mark.skipif(not shutil.which("tmux"), reason="tmux missing")
def test_server_and_worker_strip_all_parent_session_identity(monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_ID", "parent-session")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "parent-chat")
    monkeypatch.setenv("HERMES_SESSION_FUTURE_KEY", "parent-future")
    socket = f"hca-test-session-env-{uuid.uuid4().hex[:8]}"
    tm = TmuxManager(socket=socket)
    name = "hca-test-session-env-slot"
    try:
        tm.create_slot(name)
        tm.run_in_slot(
            name,
            "env | grep '^HERMES_SESSION_' || printf 'NO_SESSION_IDENTITY\\n'; sleep 30",
            unset_env=[
                "HERMES_SESSION_ID",
                "HERMES_SESSION_CHAT_ID",
                "HERMES_SESSION_FUTURE_KEY",
            ],
        )
        text = ""
        for _ in range(20):
            text = tm.capture_pane(name, lines=10)
            if "NO_SESSION_IDENTITY" in text:
                break
            time.sleep(0.05)
        assert "NO_SESSION_IDENTITY" in text
        server_env = tm._cmd("show-environment", "-g", check=False).stdout
        assert "HERMES_SESSION_" not in server_env
    finally:
        tm.kill_session(name)
