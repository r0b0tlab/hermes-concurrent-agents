#!/usr/bin/env python3
"""Run deterministic HCA orchestration acceptance and write one JSON artifact."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHECKS = (
    "tests/unit/test_parallel_planner.py",
    "tests/unit/test_resources.py",
    "tests/integration/test_vertical_slice_c1.py::test_c1_vertical_slice_completes_with_real_evidence",
    "tests/integration/test_vertical_slice_c1.py::test_parallel_acceptance_uses_distinct_workers_worktrees_and_real_overlap",
    "tests/integration/test_vertical_slice_c1.py::test_review_rejection_stages_one_bounded_rework_then_accepts",
    "tests/integration/test_vertical_slice_c1.py::test_review_rejection_budget_blocks_final_without_unbounded_loop",
    "tests/integration/test_vertical_slice_c1.py::test_needs_input_response_updates_upstream_and_resumes_exact_branch",
    "tests/integration/test_vertical_slice_c1.py::test_detached_controller_finishes_real_tmux_kanban_chain",
    "tests/integration/test_vertical_slice_c1.py::test_c1_stop_terminates_owned_worker_and_reconciles",
    "tests/integration/test_vertical_slice_c1.py::test_c1_dead_worker_is_reclaimed_and_replaced_once",
)


def _git_object(root: Path, object_name: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", object_name],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="", help="JSON report path")
    parser.add_argument(
        "--hermes-src",
        default=str(Path.home() / ".hermes" / "hermes-agent"),
        help="Hermes source tree whose real Kanban contract is exercised",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out).expanduser() if args.out else root / ".hermes" / "acceptance" / f"orchestration-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    hermes_src = Path(args.hermes_src).expanduser().resolve()
    env = os.environ.copy()
    env["HCA_HERMES_SRC"] = str(hermes_src)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="hca-acceptance-") as tmp:
        metrics_path = Path(tmp) / "parallel-metrics.json"
        env["HCA_PARALLEL_METRICS_OUT"] = str(metrics_path)
        command = [sys.executable, "-m", "pytest", "-q", *CHECKS]
        completed = subprocess.run(
            command,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        elapsed = time.perf_counter() - started
        parallel_metrics = {}
        if metrics_path.is_file():
            parallel_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

    passed = completed.returncode == 0 and bool(parallel_metrics)
    report = {
        "schema_version": 2,
        "suite": "hca-deterministic-orchestration",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_commit": _git_object(root, "HEAD"),
        "git_tree": _git_object(root, "HEAD^{tree}"),
        "python": platform.python_version(),
        "hermes_contract_source": "installed-source-tree",
        "hermes_contract_path": str(hermes_src),
        "hermes_contract_commit": _git_object(hermes_src, "HEAD"),
        "hermes_contract_tree": _git_object(hermes_src, "HEAD^{tree}"),
        "checks": list(CHECKS),
        "passed": passed,
        "pytest_exit_code": completed.returncode,
        "elapsed_seconds": elapsed,
        "parallel_metrics": parallel_metrics,
        "pytest_stdout": completed.stdout[-4000:],
        "pytest_stderr": completed.stderr[-4000:],
    }
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    print(f"acceptance_report={out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
