"""Team selection from bundled templates.

A ``TeamSpec`` is the typed, versioned description of the role composition a
run uses: a planner, a worker pool, and (optionally mandatory) an independent
reviewer. Presets live in ``templates/teams/*.toml`` and ship in the wheel.
Selection is deterministic and never fabricates roles that do not exist.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from hca.config import PACKAGE_DIR

TEAM_SCHEMA_VERSION = 1


def teams_dir() -> Path:
    return PACKAGE_DIR / "templates" / "teams"


@dataclass
class TeamRole:
    name: str
    kind: str  # planner | worker | reviewer
    count: int = 1
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TeamSpec:
    name: str
    description: str = ""
    review_policy: str = "auto"  # auto | always | never
    max_workers: int = 4
    roles: list[TeamRole] = field(default_factory=list)
    schema_version: int = TEAM_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["roles"] = [r.to_dict() for r in self.roles]
        return d

    def role_of_kind(self, kind: str) -> Optional[TeamRole]:
        for r in self.roles:
            if r.kind == kind:
                return r
        return None

    def worker_count(self) -> int:
        r = self.role_of_kind("worker")
        return r.count if r else 0

    def requires_review(self) -> bool:
        return self.review_policy == "always" or self.role_of_kind("reviewer") is not None


class TeamError(RuntimeError):
    pass


def available_teams() -> list[str]:
    d = teams_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.toml"))


def _team_from_dict(data: dict[str, Any]) -> TeamSpec:
    roles: list[TeamRole] = []
    raw_roles = data.get("roles", {}) or {}
    # roles is a table of name -> {kind, count, description}
    for name, spec in raw_roles.items():
        if not isinstance(spec, dict):
            continue
        roles.append(
            TeamRole(
                name=str(name),
                kind=str(spec.get("kind", name)),
                count=int(spec.get("count", 1)),
                description=str(spec.get("description", "")),
            )
        )
    return TeamSpec(
        name=str(data.get("name", "default")),
        description=str(data.get("description", "")),
        review_policy=str(data.get("review_policy", "auto")),
        max_workers=int(data.get("max_workers", 4)),
        roles=roles,
    )


def load_team(name: str) -> TeamSpec:
    """Load a team template by name; raise TeamError with the known set."""
    if not name:
        name = "default"
    path = teams_dir() / f"{name}.toml"
    if not path.is_file():
        raise TeamError(
            f"unknown team {name!r}; available: {', '.join(available_teams()) or '(none)'}"
        )
    with path.open("rb") as f:
        data = tomllib.load(f)
    return _team_from_dict(data)


def select_team(name: str, *, review_policy: str = "") -> TeamSpec:
    """Load a team and optionally override its review policy from the run."""
    team = load_team(name)
    if review_policy and review_policy in ("auto", "always", "never"):
        team.review_policy = review_policy
    return team
