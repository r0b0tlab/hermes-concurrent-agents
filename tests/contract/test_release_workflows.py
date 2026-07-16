"""Release workflow contracts: required gates must be explicit and unmasked."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
HARDWARE = ROOT / ".github" / "workflows" / "hardware-benchmark.yml"
PINNED_HERMES_TAG = "v2026.7.7.2"
PINNED_HERMES_COMMIT = "9de9c25f620ff7f1ce0fd5457d596052d5159596"


def test_required_contract_lane_is_pinned_and_never_masked():
    text = CI.read_text(encoding="utf-8")
    assert "stable-hermes-contract:" in text
    assert PINNED_HERMES_TAG in text
    assert PINNED_HERMES_COMMIT in text
    assert re.search(r"pytest\s+-q\s+tests/contract", text)
    assert not re.search(r"pytest[^\n]*tests/contract[^\n]*(?:\|\|\s*true|continue-on-error)", text)

    stable_job = text.split("stable-hermes-contract:", 1)[1].split(
        "latest-hermes-advisory:", 1
    )[0]
    assert "continue-on-error" not in stable_job


def test_ci_has_static_advisory_package_generic_and_portable_lanes():
    text = CI.read_text(encoding="utf-8")
    for job in (
        "unit-static:",
        "latest-hermes-advisory:",
        "package-wheel:",
        "generic-integration:",
        "macos-portable:",
    ):
        assert job in text

    advisory = text.split("latest-hermes-advisory:", 1)[1].split("package-wheel:", 1)[0]
    assert "continue-on-error: true" in advisory
    package = text.split("package-wheel:", 1)[1].split("generic-integration:", 1)[0]
    assert "python -m build" in package
    assert "hermes_agent.plugins" in package
    assert "--no-deps" not in package
    assert "scripts/release-check.sh --quick" in text
    release = (ROOT / "scripts" / "release-check.sh").read_text()
    assert "pyright" in release
    assert "actionlint" in release
    assert "HCA_HERMES_SRC" in release
    assert "|| true" not in text

def test_hardware_workflow_is_manual_and_never_publishes():
    text = HARDWARE.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "self-hosted" in text and "gb10" in text.lower()
    assert "contents: read" in text
    assert "${{ runner.temp }}" not in text
    assert "RUNNER_TEMP" in text
    assert ".hardware-venv" not in text
    assert "upload-artifact" not in text
    forbidden = ("twine upload", "gh release create", "git push", "docker push")
    assert not any(command in text for command in forbidden)
