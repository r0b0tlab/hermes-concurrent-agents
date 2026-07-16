"""Exact Linux process identity helpers used by worker/controller ownership.

A PID alone is not an ownership token because the kernel can reuse it.  HCA
pairs every owned PID with field 22 (process start time in clock ticks) from
``/proc/<pid>/stat`` and refuses to signal a process when the pair differs.
"""

from __future__ import annotations

from pathlib import Path


def _proc_stat(pid: int) -> tuple[int, int, str] | None:
    """Return ``(start_ticks, process_group, state)`` from Linux procfs."""

    try:
        text = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        tail = text.rsplit(")", 1)[1].strip().split()
        return int(tail[19]), int(tail[2]), tail[0]
    except (OSError, ValueError, IndexError):
        return None


def proc_start_ticks(pid: int) -> int | None:
    """Return a live non-zombie process's Linux start ticks, else ``None``.

    ``comm`` in proc stat is parenthesized and may contain spaces, so ordinary
    ``split()`` indexing is unsafe. Fields after the final ``)`` begin at field
    3 (state); process group is field 5 and starttime is field 22.
    """

    stat = _proc_stat(pid)
    if stat is None or stat[2] == "Z":
        return None
    return stat[0]


def process_group_alive(pgid: int) -> bool:
    """True when a process group contains at least one non-zombie member."""

    try:
        entries = Path("/proc").iterdir()
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        stat = _proc_stat(int(entry.name))
        if stat is not None and stat[1] == int(pgid) and stat[2] != "Z":
            return True
    return False


def process_identity_matches(pid: int | None, start_ticks: int | None) -> bool:
    """True only when ``pid`` still denotes the exact recorded process."""

    if pid is None or start_ticks is None:
        return False
    try:
        expected = int(start_ticks)
        value = int(pid)
    except (TypeError, ValueError):
        return False
    return value > 0 and proc_start_ticks(value) == expected
