#!/usr/bin/env python3
"""Fail a release when public tracked/unignored source violates safety rules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hca.release_safety import scan_paths  # noqa: E402


def candidate_paths() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in proc.stdout.split(b"\0") if item]


def main() -> int:
    findings = scan_paths(ROOT, candidate_paths())
    if findings:
        for finding in findings:
            print(
                f"[{finding.rule}] {finding.path}:{finding.line}: {finding.excerpt}",
                file=sys.stderr,
            )
        print(f"public safety check failed: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("public safety check PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
