#!/usr/bin/env python3
"""Generate or verify docs/support-matrix.md from executable support data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hca.support import render_support_matrix  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / "docs" / "support-matrix.md"
    expected = render_support_matrix()
    if args.check:
        actual = path.read_text(encoding="utf-8") if path.is_file() else ""
        if actual != expected:
            print(f"generated support matrix is stale: {path}", file=sys.stderr)
            return 1
        print("support matrix is current")
        return 0
    path.write_text(expected, encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
