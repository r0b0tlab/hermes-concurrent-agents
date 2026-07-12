"""Run log helpers (pipe-pane files under state_dir/logs)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator


def log_dir(state_dir: str) -> Path:
    p = Path(state_dir).expanduser() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_path(state_dir: str, run_id: str) -> Path:
    return log_dir(state_dir) / f"{run_id}.log"


def append_log(state_dir: str, run_id: str, text: str) -> Path:
    path = log_path(state_dir, run_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")
    return path


def read_log(state_dir: str, run_id: str, *, tail: int = 200) -> str:
    path = log_path(state_dir, run_id)
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-tail:])


def follow_log(state_dir: str, run_id: str, *, poll: float = 0.5) -> Iterator[str]:
    path = log_path(state_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                yield line.rstrip("\n")
            else:
                time.sleep(poll)
