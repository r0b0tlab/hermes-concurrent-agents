"""Public-release source safety checks with structured findings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SafetyFinding:
    path: str
    line: int
    rule: str
    excerpt: str


_SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
)
_CREDENTIAL_URL = re.compile(r"https?://[^/\s:@]+:[^@\s/]+@[^\s]+")
_PRIVATE_HOME = re.compile(r"(?<![\w])/(?:home|Users)/([A-Za-z0-9._-]+)(?:/|\b)")
_REQUIRED_MASK = re.compile(
    r"(?:pytest[^\n]*tests/contract|scripts/release-check\.sh)[^\n]*\|\|\s*true"
)
_REMOTE_START = re.compile(
    r"(?:hca\s+cluster\s+nodes\s+up|hca\s+up\s+--role\s+(?:control|node)|"
    r"--preset\s+gb10-cluster-(?:vllm|sglang))"
)
_APPROVAL_BYPASS = re.compile(r"(?:^|\s)--yolo(?:\s|$)")
_ALLOWED_HOME_NAMES = {"user", "runner", "<user>", "$USER", "${USER}"}
_STABLE_SURFACES = {
    "README.md",
    "SKILL.md",
    "docs/current-state-report.md",
    "docs/deployment-guide.md",
    "docs/nvidia-playbooks.md",
    "docs/workflow-patterns.md",
}
_TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def scan_text(path: str, text: str) -> list[SafetyFinding]:
    """Scan one public text file without printing or mutating anything."""
    findings: list[SafetyFinding] = []
    normalized = path.replace("\\", "/")
    if normalized.startswith(("config/vllm/", "config/sglang/")):
        findings.append(
            SafetyFinding(
                normalized,
                0,
                "model-server-provisioning",
                "HCA consumes existing endpoints; model launch assets are out of scope",
            )
        )
    if normalized.startswith("docs/plans/"):
        header = text[:500].lower()
        if "historical" not in header or not any(
            marker in header for marker in ("superseded", "not executable")
        ):
            findings.append(
                SafetyFinding(
                    normalized,
                    1,
                    "historical-plan-marker",
                    "retained plans must be explicitly historical and non-authoritative",
                )
            )
    is_test = normalized.startswith("tests/")
    for number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in _SECRET_RULES:
            if pattern.search(line):
                findings.append(SafetyFinding(normalized, number, rule, line.strip()[:200]))
        if not is_test and _CREDENTIAL_URL.search(line):
            findings.append(
                SafetyFinding(normalized, number, "credential-url", line.strip()[:200])
            )
        if not is_test:
            for match in _PRIVATE_HOME.finditer(line):
                if match.group(1) not in _ALLOWED_HOME_NAMES:
                    findings.append(
                        SafetyFinding(normalized, number, "private-home", line.strip()[:200])
                    )
        if _REQUIRED_MASK.search(line):
            findings.append(
                SafetyFinding(normalized, number, "masked-required-check", line.strip()[:200])
            )
        if normalized in _STABLE_SURFACES and _REMOTE_START.search(line):
            findings.append(
                SafetyFinding(
                    normalized,
                    number,
                    "unsupported-remote-placement-claim",
                    line.strip()[:200],
                )
            )
        if (
            not is_test
            and normalized.startswith("scripts/")
            and _APPROVAL_BYPASS.search(line)
        ):
            findings.append(
                SafetyFinding(normalized, number, "approval-bypass", line.strip()[:200])
            )
    return findings


def scan_paths(root: Path, paths: Iterable[str]) -> list[SafetyFinding]:
    """Scan explicit repository-relative paths; unreadable/binary files fail closed."""
    findings: list[SafetyFinding] = []
    for relative in sorted(set(paths)):
        path = root / relative
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            findings.append(
                SafetyFinding(relative, 0, "unreadable-public-text", type(exc).__name__)
            )
            continue
        findings.extend(scan_text(relative, text))
    return findings
