"""Logical-role → concrete-slot routing with pre-reservation.

The dispatchable Hermes identity is a *concrete profile slot*
(``hca-<fleet>-<role>-NN``), which is simultaneously the profile directory,
the tmux session, and the isolation identity. Logical roles (``coder``,
``research``, ``reviewer`` …) are HCA routing metadata that resolve to an
*eligible free* concrete slot — never a fake Hermes assignee, and never a
silent fallback to a generic ``coder`` profile.

Reservation-first is the safety invariant: a slot (and its resource credit)
is reserved *before* the upstream claim, so the spawn callback has no
admission decision left to make — it launches its pre-reserved slot or
raises. Because upstream ``dispatch_once`` records a claimed task as
``spawned`` even when the callback returns a falsy PID, a callback that
cannot proceed must **raise** (auto-blocking the task visibly) rather than
return ``None`` (an invisible stuck claim).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from hca.config import FleetConfig
from hca.observe import slot_name
from hca.state import StateDB

# Kinds of concrete slot.
KIND_PLANNER = "planner"
KIND_WORKER = "worker"
KIND_REVIEWER = "reviewer"

# The concrete slot role that plays each special kind. Everything that is
# not the planner or the reviewer role is an execution worker.
PLANNER_ROLE = "orchestrator"
REVIEWER_ROLE = "qa"

# Logical task-role hints → the concrete slot *role* eligible to run them.
# Absent hints resolve to a generic execution worker; an *unknown* explicit
# hint is unroutable (fail visibly) rather than silently coerced to coder.
ROLE_ALIASES: dict[str, str] = {
    "planner": "orchestrator",
    "orchestrator": "orchestrator",
    "reviewer": "qa",
    "review": "qa",
    "qa": "qa",
    "test": "qa",
    "testing": "qa",
    "coder": "coder",
    "coding": "coder",
    "code": "coder",
    "implement": "coder",
    "research": "research",
    "researcher": "research",
    "investigate": "research",
    "docs": "creative",
    "documentation": "creative",
    "writing": "creative",
    "creative": "creative",
    "worker": "",  # any execution worker; defaults put general slots first
    "general": "general",
}


class RoutingError(RuntimeError):
    """Raised when a task cannot be routed to any concrete slot."""


@dataclass(frozen=True)
class SlotIdentity:
    profile: str
    fleet: str
    role: str
    index: int
    kind: str

    @property
    def slot(self) -> str:
        # The tmux session / live-slot key is the sanitized profile name.
        return self.profile


def _kind_for_role(role: str) -> str:
    if role == PLANNER_ROLE:
        return KIND_PLANNER
    if role == REVIEWER_ROLE:
        return KIND_REVIEWER
    return KIND_WORKER


def concrete_slots(cfg: FleetConfig) -> list[SlotIdentity]:
    """Enumerate every concrete dispatch slot from the fleet config."""
    out: list[SlotIdentity] = []
    for role, count in cfg.profile_slots.items():
        for i in range(1, int(count) + 1):
            profile = slot_name(cfg.name, role, i)
            out.append(
                SlotIdentity(
                    profile=profile,
                    fleet=cfg.name,
                    role=role,
                    index=i,
                    kind=_kind_for_role(role),
                )
            )
    return out


def slots_of_kind(cfg: FleetConfig, kind: str) -> list[SlotIdentity]:
    return [s for s in concrete_slots(cfg) if s.kind == kind]


def planner_slots(cfg: FleetConfig) -> list[SlotIdentity]:
    return slots_of_kind(cfg, KIND_PLANNER)


def worker_slots(cfg: FleetConfig) -> list[SlotIdentity]:
    return slots_of_kind(cfg, KIND_WORKER)


def reviewer_slots(cfg: FleetConfig) -> list[SlotIdentity]:
    return slots_of_kind(cfg, KIND_REVIEWER)


def resolve_role_hint(hint: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return ``(concrete_role_or_empty, error)``.

    - ``("", None)``: no/any hint → any execution worker is eligible.
    - ``("coder", None)``: a specific concrete role is required.
    - ``(None, reason)``: an *unknown* hint — fail visibly, do not fall back.
    """
    if hint is None:
        return "", None
    key = str(hint).strip().lower()
    if not key:
        return "", None
    if key in ROLE_ALIASES:
        return ROLE_ALIASES[key], None
    return None, (
        f"unknown role/requirement {hint!r}: no concrete slot maps to it. "
        f"Known hints: {', '.join(sorted(ROLE_ALIASES))}. Refusing to run it "
        "on an arbitrary fallback profile."
    )


@dataclass
class Reservations:
    """Slots reserved this tick, on top of the durable running set."""

    reserved: set[str] = field(default_factory=set)

    def busy(self, state: StateDB) -> set[str]:
        running = {r.slot for r in state.list_runs(status="running")}
        return running | set(self.reserved)

    def reserve(self, profile: str) -> None:
        self.reserved.add(profile)

    def release(self, profile: str) -> None:
        self.reserved.discard(profile)


@dataclass
class Assignment:
    task_id: str
    profile: str
    role_hint: str
    kind: str


@dataclass
class Unroutable:
    task_id: str
    reason: str


def _eligible_worker_pool(
    cfg: FleetConfig, concrete_role: str
) -> list[SlotIdentity]:
    """Concrete slots eligible for a role hint (no fallback coercion)."""
    if concrete_role in ("", None):
        # any execution worker
        pool = worker_slots(cfg)
        # if a fleet has no dedicated worker slots, fall back to any
        # non-planner/non-reviewer concrete slot, else all slots.
        return pool or [s for s in concrete_slots(cfg) if s.kind == KIND_WORKER]
    return [s for s in concrete_slots(cfg) if s.role == concrete_role]


def route_task(
    cfg: FleetConfig,
    state: StateDB,
    reservations: Reservations,
    *,
    task_id: str,
    role_hint: Optional[str] = None,
) -> Assignment | Unroutable:
    """Resolve one task's logical role to a free concrete slot and reserve it.

    Reservation is applied on success so the concurrent routing of the same
    tick cannot double-book a slot. Callers release unused reservations
    after dispatch binds real runs.
    """
    concrete_role, err = resolve_role_hint(role_hint)
    if err:
        return Unroutable(task_id=task_id, reason=err)

    pool = _eligible_worker_pool(cfg, concrete_role or "")
    if not pool:
        return Unroutable(
            task_id=task_id,
            reason=(
                f"no concrete slot exists for role {role_hint!r} in fleet "
                f"{cfg.name!r} — add a matching profile slot or change the "
                "task's required role"
            ),
        )
    busy = reservations.busy(state)
    for slot in pool:
        if slot.profile not in busy:
            reservations.reserve(slot.profile)
            return Assignment(
                task_id=task_id,
                profile=slot.profile,
                role_hint=concrete_role or "worker",
                kind=slot.kind,
            )
    return Unroutable(
        task_id=task_id,
        reason=(
            f"all {len(pool)} eligible slot(s) for role "
            f"{role_hint or 'worker'!r} are busy — task stays ready "
            "(capacity), no fallback profile used"
        ),
    )


def free_slot_for_kind(
    cfg: FleetConfig, state: StateDB, reservations: Reservations, kind: str
) -> Optional[SlotIdentity]:
    """First free concrete slot of a given kind (planner/worker/reviewer)."""
    busy = reservations.busy(state)
    for slot in slots_of_kind(cfg, kind):
        if slot.profile not in busy:
            reservations.reserve(slot.profile)
            return slot
    return None
