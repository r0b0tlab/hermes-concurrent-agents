"""Run log helpers (pipe-pane files under state_dir/logs)."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Iterator


def log_dir(state_dir: str) -> Path:
    p = Path(state_dir).expanduser() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_path(state_dir: str, run_id: str) -> Path:
    return log_dir(state_dir) / f"{run_id}.log"


def worker_log_id(board: str, task_id: str, run_id: object) -> str:
    """Return a traversal-free globally unique worker log identity.

    Upstream run IDs are board-local integers, so ``2.log`` aliases every
    board's first dispatched task. Include board and task ownership to prevent
    cross-run evidence from being appended into an unrelated log.
    """
    raw = f"{board}--{task_id}--{run_id}"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._")[:180] or "worker"
    if safe != raw:
        safe = f"{safe}-{hashlib.sha256(raw.encode()).hexdigest()[:10]}"
    return safe


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
